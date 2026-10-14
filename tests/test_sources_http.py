import email.utils
import os
import threading
import time
from pathlib import Path

import pytest

from edmultitool.sources.http import (
    MAX_DELAY,
    BadReply,
    Cancelled,
    HttpError,
    NetworkError,
    PoliteClient,
    Response,
)
from fakes import FakeTime, ScriptedTransport, make_client, network_down

URL = "https://example.invalid/api"
OK = Response(200, b'{"ok": true}')


def test_every_request_carries_user_agent_and_timeout() -> None:
    t = ScriptedTransport(OK, OK)
    client = make_client(t, FakeTime(), timeout=7.5)
    client.get(URL, threading.Event(), params=[("q", "Sol")])
    client.post_form(URL, [("from", "Sol")], threading.Event())
    assert [r.headers["User-Agent"] for r in t.requests] == ["EDMultitool-test/1"] * 2
    assert t.timeouts == [7.5, 7.5]
    assert t.requests[0].params == [("q", "Sol")]
    assert t.requests[1].method == "POST" and t.requests[1].form == [("from", "Sol")]


def test_requests_are_paced_one_per_interval() -> None:
    fake = FakeTime()
    client = make_client(ScriptedTransport(OK, OK, OK), fake, min_interval=1.0)
    for _ in range(3):
        client.get(URL, threading.Event())
    assert fake.waits == [1.0, 1.0]  # nothing before the first call, one second before each next


def test_429_honours_retry_after_then_succeeds() -> None:
    fake = FakeTime()
    t = ScriptedTransport(Response(429, b"", {"retry-after": "5"}), OK)
    resp = make_client(t, fake, min_interval=0).get(URL, threading.Event())
    assert resp.status == 200
    assert len(t.requests) == 2
    assert fake.waits == [5.0]


def test_retry_after_longer_than_we_wait_gives_up_at_once() -> None:
    fake = FakeTime()
    t = ScriptedTransport(Response(503, b"", {"retry-after": str(MAX_DELAY + 1)}), OK)
    with pytest.raises(HttpError, match="asks to wait"):
        make_client(t, fake, min_interval=0).get(URL, threading.Event())
    assert len(t.requests) == 1 and fake.waits == []


def test_retry_after_as_http_date() -> None:
    fake = FakeTime()
    when = email.utils.formatdate(fake.now + 5, usegmt=True)
    t = ScriptedTransport(Response(429, b"", {"retry-after": when}), OK)
    assert make_client(t, fake, min_interval=0).get(URL, threading.Event()).status == 200
    assert fake.waits == [5.0]


def test_retry_after_http_date_without_zone_is_utc() -> None:
    fake = FakeTime()
    when = email.utils.formatdate(fake.now + 5)  # "... -0000": no zone name
    t = ScriptedTransport(Response(429, b"", {"retry-after": when}), OK)
    make_client(t, fake, min_interval=0).get(URL, threading.Event())
    assert fake.waits == [5.0]


def test_post_is_not_retried_when_the_server_may_have_taken_it() -> None:
    """A lost reply or a gateway error after a submit must not start a duplicate Spansh job."""
    down = ScriptedTransport(network_down())
    with pytest.raises(NetworkError):
        make_client(down, FakeTime(), min_interval=0).post_form(URL, [("a", "1")], threading.Event())
    assert len(down.requests) == 1
    gateway = ScriptedTransport(Response(502, b""))
    assert make_client(gateway, FakeTime()).post_form(URL, [("a", "1")], threading.Event()).status == 502
    assert len(gateway.requests) == 1


def test_post_is_retried_when_the_server_refused_it() -> None:
    fake = FakeTime()
    t = ScriptedTransport(Response(429, b""), Response(503, b""), OK)
    assert make_client(t, fake, min_interval=0).post_form(URL, [("a", "1")], threading.Event()).status == 200
    assert fake.waits == [2.0, 4.0]


def test_5xx_backs_off_2_4_8_then_gives_up() -> None:
    fake = FakeTime()
    t = ScriptedTransport(Response(502, b""), repeat_last=True)
    with pytest.raises(HttpError) as err:
        make_client(t, fake, min_interval=0).get(URL, threading.Event())
    assert err.value.status == 502
    assert len(t.requests) == 4  # first try + 3 retries
    assert fake.waits == [2.0, 4.0, 8.0]


def test_network_failure_is_retried() -> None:
    fake = FakeTime()
    t = ScriptedTransport(network_down(), OK)
    assert make_client(t, fake, min_interval=0).get(URL, threading.Event()).status == 200
    assert fake.waits == [2.0]


def test_network_failure_gives_up_as_network_error() -> None:
    t = ScriptedTransport(network_down(), repeat_last=True)
    with pytest.raises(NetworkError):
        make_client(t, FakeTime(), min_interval=0).get(URL, threading.Event())
    assert len(t.requests) == 4


@pytest.mark.parametrize("status", [400, 404])
def test_client_errors_are_returned_not_retried(status: int) -> None:
    t = ScriptedTransport(Response(status, b'{"error": "nope"}'))
    assert make_client(t, FakeTime()).get(URL, threading.Event()).status == status
    assert len(t.requests) == 1


def test_cache_serves_fresh_replies_from_disk_across_clients(tmp_path: Path) -> None:
    fake = FakeTime()
    t = ScriptedTransport(OK, Response(200, b'{"ok": "again"}'))
    first = make_client(t, fake, cache_dir=tmp_path)
    assert first.get(URL, threading.Event(), params=[("q", "Sol")], ttl=60).from_cache is False
    # A new client (an EDMC restart) still finds the reply on disk.
    second = make_client(t, fake, cache_dir=tmp_path)
    hit = second.get(URL, threading.Event(), params=[("q", "Sol")], ttl=60)
    assert hit.from_cache is True and hit.json() == {"ok": True}
    assert len(t.requests) == 1
    # Different parameters are a different entry; an expired entry is fetched again.
    fake.now += 61
    assert second.get(URL, threading.Event(), params=[("q", "Sol")], ttl=60).json() == {"ok": "again"}
    assert len(t.requests) == 2


def test_only_200_replies_are_cached(tmp_path: Path) -> None:
    t = ScriptedTransport(Response(404, b"{}"), OK)
    client = make_client(t, FakeTime(), cache_dir=tmp_path)
    assert client.get(URL, threading.Event(), ttl=60).status == 404
    assert client.get(URL, threading.Event(), ttl=60).status == 200
    assert len(t.requests) == 2


def test_a_200_maintenance_page_is_not_remembered(tmp_path: Path) -> None:
    page = Response(200, b"<html>Down for maintenance</html>")
    t = ScriptedTransport(page, OK)
    client = make_client(t, FakeTime(), cache_dir=tmp_path)
    assert client.get(URL, threading.Event(), ttl=60).body == page.body
    second = client.get(URL, threading.Event(), ttl=60)
    assert second.from_cache is False and second.json() == {"ok": True}
    assert len(t.requests) == 2


def test_a_reply_the_caller_rejects_is_neither_remembered_nor_served(tmp_path: Path) -> None:
    def valid(doc: object) -> bool:
        if doc != {"ok": True}:
            raise BadReply("wrong shape")
        return True

    t = ScriptedTransport(Response(200, b'{"broken": 1}'), OK, OK)
    client = make_client(t, FakeTime(), cache_dir=tmp_path)
    assert client.get(URL, threading.Event(), ttl=60, valid=valid).json() == {"broken": 1}
    assert client.get(URL, threading.Event(), ttl=60, valid=valid).from_cache is False
    assert client.get(URL, threading.Event(), ttl=60, valid=valid).from_cache is True
    assert len(t.requests) == 2
    # A bad body already on disk (written by an older version) is fetched again, not served.
    client.cache_put("GET " + URL + "?", "<html>maintenance</html>")
    assert client.get(URL, threading.Event(), ttl=60, valid=valid).from_cache is False
    assert len(t.requests) == 3


def test_damaged_cache_file_is_a_miss_and_logged_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    client = make_client(ScriptedTransport(OK, OK), FakeTime(), cache_dir=tmp_path)
    client.cache_put("a", "x")
    client.cache_put("b", "y")
    for path in tmp_path.iterdir():
        path.write_text("{not json", encoding="utf-8")
    assert client.cache_get("a", 60) is None
    assert client.cache_get("b", 60) is None
    assert sum("cache" in r.message for r in caplog.records) == 1


def test_prune_removes_only_old_files(tmp_path: Path) -> None:
    fake = FakeTime()
    fake.now = time.time()
    client = make_client(ScriptedTransport(), fake, cache_dir=tmp_path)
    client.cache_put("old", "1")
    client.cache_put("new", "2")
    old = next(p for p in tmp_path.iterdir() if '"old"' in p.read_text(encoding="utf-8"))
    os.utime(old, (fake.now - 7200, fake.now - 7200))
    assert client.prune_cache(3600) == 1
    assert client.cache_get("new", 60) == "2"
    assert client.cache_get("old", 10**9) is None


def test_cancel_before_send_makes_no_call() -> None:
    t = ScriptedTransport(OK)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        make_client(t, FakeTime()).get(URL, cancel)
    assert t.requests == []


def test_cancel_interrupts_a_real_backoff_wait_at_once() -> None:
    """Real clock and real waits: a 503 starts a 2 s backoff; cancelling ends it immediately."""
    t = ScriptedTransport(Response(503, b""), repeat_last=True)
    client = PoliteClient(t, user_agent="EDMultitool-test/1", min_interval=0)
    cancel = threading.Event()
    threading.Timer(0.2, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(Cancelled):
        client.get(URL, cancel)
    assert time.monotonic() - started < 1.0
    assert len(t.requests) == 1
