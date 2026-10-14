"""The "a new version is out" check: version order, GitHub's reply, the plugin's quiet handling of it."""

import json
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from edmultitool import __version__
from edmultitool.core.settings import normalise, parse_update_check
from edmultitool.core.updates import UiUpdate, is_newer, parse_release, parse_version
from edmultitool.plugin import UPDATE_EVERY, UPDATE_RETRY, ScoutPlugin
from edmultitool.sources import github
from edmultitool.sources.http import BadReply, HttpError, NetworkError, PoliteClient, Request, Response
from fakes import FakeTime, ScriptedTransport, make_client, network_down

FIXTURE = Path(__file__).parent / "data" / "github" / "latest_release.json"
RELEASE_PAGE = "https://github.com/Martin8O/ED-Multitool/releases/tag/v99.1.0"


def _doc(tag: str = "v99.1.0", **over: object) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))["body"]
    return {**body, "tag_name": tag, **over}


def _ok(doc: object) -> Response:
    return Response(200, json.dumps(doc).encode("utf-8"))


@pytest.mark.parametrize(
    ("current", "latest", "newer"),
    [
        ("0.30.0", "v0.31.0", True),
        ("0.30.0", "0.30.0", False),
        ("0.31.0", "v0.30.0", False),
        ("0.9.0", "0.10.0", True),  # numbers, not text
        ("0.30.0", "v1.0", True),  # a missing patch is 0
        ("1.0.0-beta.1", "1.0.0", True),  # the final release is newer than its pre-release
        ("1.0.0", "1.0.0-beta.1", False),
        ("1.0.0-beta.2", "1.0.0-beta.10", True),  # numbers inside a pre-release compare as numbers
        ("1.0.0-alpha", "1.0.0-beta", True),
        ("1.0.0-1", "1.0.0-alpha", True),  # numbers come before words
        ("1.0.0-beta", "1.0.0-beta.1", True),  # a longer list is newer
        ("0.30.0", "Release/6.1.2", False),  # unreadable: never nag
        ("junk", "1.0.0", False),
        ("0.30.0", "", False),
    ],
)
def test_version_order(current: str, latest: str, newer: bool) -> None:
    assert is_newer(current, latest) is newer


def test_version_parsing() -> None:
    assert parse_version("v1.2.3-rc.1+build5") == (1, 2, 3, ("rc", "1"))
    assert parse_version(" 2.0 ") == (2, 0, 0, ())
    assert parse_version("1.2.3.4") is None
    assert parse_version("1.x") is None


def test_release_reply_is_read_carefully() -> None:
    release = parse_release(_doc())
    assert release is not None
    assert (release.version, release.tag, release.name) == ("99.1.0", "v99.1.0", "ED Multitool 99.1.0")
    assert release.url == RELEASE_PAGE
    assert parse_release(_doc(draft=True)) is None
    assert parse_release(_doc(prerelease=True)) is None
    assert parse_release(_doc("nightly")) is None
    assert parse_release(_doc(html_url="https://evil.example/x")) is None
    assert parse_release(_doc(html_url=None)) is None
    assert parse_release([]) is None
    assert parse_release({"message": "Not Found"}) is None


def _client(
    *steps: Response | Exception, cache: Path | None = None
) -> tuple[PoliteClient, ScriptedTransport]:
    t = ScriptedTransport(*steps)
    return make_client(t, FakeTime(), cache), t


def test_source_reads_the_latest_release() -> None:
    client, t = _client(_ok(_doc()))
    release = github.latest_release(client, threading.Event())
    assert release is not None and release.version == "99.1.0"
    assert t.requests[0].url == github.URL
    assert t.requests[0].headers["User-Agent"].startswith("EDMultitool")
    assert "Authorization" not in t.requests[0].headers


def test_source_says_nothing_when_there_is_no_release_yet() -> None:
    client, _ = _client(Response(404, b'{"message": "Not Found"}'))
    assert github.latest_release(client, threading.Event()) is None


def test_source_failures_raise() -> None:
    cancel = threading.Event()
    client, _ = _client(Response(403, b'{"message": "rate limit exceeded"}'))
    with pytest.raises(HttpError):
        github.latest_release(client, cancel)
    client, _ = _client(*[Response(503, b"{}")] * 5)
    with pytest.raises(HttpError):
        github.latest_release(client, cancel)
    client, _ = _client(*[network_down()] * 5)
    with pytest.raises(NetworkError):
        github.latest_release(client, cancel)
    client, _ = _client(_ok({"tag_name": "nightly"}))
    with pytest.raises(BadReply):
        github.latest_release(client, cancel)


def test_a_reply_is_asked_for_once_a_day(tmp_path: Path) -> None:
    client, t = _client(_ok(_doc()), cache=tmp_path)
    cancel = threading.Event()
    assert github.latest_release(client, cancel) is not None
    assert github.latest_release(client, cancel) is not None  # from the cache
    assert len(t.requests) == 1


def test_setting_defaults_and_validation() -> None:
    assert normalise({}).update_check is True
    assert normalise({"update_check": False}).update_check is False
    assert normalise({"update_check": "no"}).update_check is True
    assert parse_update_check({"on": False}) is False
    assert isinstance(parse_update_check({"on": 1}), str)
    assert isinstance(parse_update_check([]), str)


# --- the plugin -----------------------------------------------------------------------------------

Route = Callable[[Request], Response]
Make = Callable[[Route], tuple[ScoutPlugin, ScriptedTransport]]


@pytest.fixture
def make_plugin(tmp_path: Path) -> Iterator[Make]:
    made: list[ScoutPlugin] = []

    def make(route: Route) -> tuple[ScoutPlugin, ScriptedTransport]:
        p = ScoutPlugin(tmp_path / "plugin" / str(len(made)))
        t = ScriptedTransport(route, repeat_last=True)
        p.client = make_client(t, FakeTime())
        p.start_worker()
        made.append(p)
        return p, t

    yield make
    for p in made:
        p.stop_worker()
        p.close()


def _pump_until(p: ScoutPlugin, done: Callable[[], object], timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while not done() and time.monotonic() < deadline:
        p.pump()
        time.sleep(0.01)
    assert done()


def _github(doc: object, status: int = 200) -> Route:
    def route(request: Request) -> Response:
        if request.url == github.URL:
            return Response(status, json.dumps(doc).encode("utf-8"))
        return Response(500, b"{}")

    return route


def test_a_newer_release_shows_on_the_page_and_the_panel(make_plugin: Make) -> None:
    p, t = make_plugin(_github(_doc()))
    p._want_update()
    _pump_until(p, lambda: p.ui.update.version != "")
    assert p.ui.update == UiUpdate("99.1.0", RELEASE_PAGE)
    assert p.text.update.startswith("Version 99.1.0 is out")
    assert p.ui.update_check is True
    assert [r.url for r in t.requests] == [github.URL]
    p._want_update()  # asked again at once: not before a day has passed
    p.pump()
    assert len(t.requests) == 1


@pytest.mark.parametrize(
    ("doc", "status"),
    [
        (_doc(__version__), 200),  # the same version
        (_doc("v0.0.1"), 200),  # an older one
        ({"message": "Not Found"}, 404),  # no release yet / the repository is still private
        ({"message": "rate limit"}, 403),
        ({"tag_name": "nightly"}, 200),
    ],
)
def test_nothing_is_shown_unless_a_newer_release_exists(make_plugin: Make, doc: object, status: int) -> None:
    p, t = make_plugin(_github(doc, status))
    p._want_update()
    _pump_until(p, lambda: p._update_job is None and t.requests)
    assert p.ui.update == UiUpdate()
    assert p.text.update == ""
    assert not any("version" in n.lower() for n in p.ui.notices)  # quiet: not even a notice


def test_offline_shows_nothing_and_asks_again_in_an_hour(make_plugin: Make) -> None:
    def down(request: Request) -> Response:
        raise network_down()

    p, _ = make_plugin(down)
    p._want_update()
    _pump_until(p, lambda: p._update_job is None and p._update_at is not None)
    assert p.ui.update == UiUpdate()
    assert p._update_at is not None
    due_in = UPDATE_EVERY - (time.monotonic() - p._update_at)
    assert UPDATE_RETRY - 5 < due_in <= UPDATE_RETRY


def test_turning_the_check_off_hides_the_strip_and_stops_asking(make_plugin: Make) -> None:
    p, t = make_plugin(_github(_doc()))
    p._want_update()
    _pump_until(p, lambda: p.ui.update.version != "")
    assert p.api_post("/api/update-check", {"on": False}) == (202, {"accepted": "update-check"})
    p.pump()
    assert p.settings.update_check is False
    assert p.ui.update == UiUpdate() and p.ui.update_check is False and p.text.update == ""
    assert '"update_check": false' in p.settings_path.read_text(encoding="utf-8")
    before = len(t.requests)
    p._want_update()
    assert len(t.requests) == before
    bad = p.api_post("/api/update-check", {"on": "yes"})
    assert bad is not None and bad[0] == 400
    # switched on again: it asks at once
    p.api_post("/api/update-check", {"on": True})
    p.pump()
    _pump_until(p, lambda: p.ui.update.version != "")
