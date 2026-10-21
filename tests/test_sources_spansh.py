"""Spansh adapter on trimmed real replies (tests/data/spansh/, recorded by tools/smoke_spansh.py)."""

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from edmultitool.core.scout import ScoutKind, ScoutQuery
from edmultitool.sources import spansh
from edmultitool.sources.http import BadReply, Cancelled, HttpError, Request, Response, SourceError
from fakes import FakeTime, ScriptedTransport, make_client, recorded, reply

SOL = 10477373803
COLONIA = 3238296097059


def _query(kind: str) -> ScoutQuery:
    """The query the recordings were made with, rebuilt from the recorded form."""
    form = dict(recorded(f"{kind}_submit")["request"]["form"])
    return ScoutQuery(
        form["from"],
        form["to"],
        float(form["range"]),
        radius_ly=float(form["radius"]),
        max_results=int(form["max_results"]),
        max_distance_ls=int(form["max_distance"]),
        min_value=int(form["min_value"]),
    )


def _run(
    kind: ScoutKind, *steps: Any, cache_dir: Path | None = None, fake: FakeTime | None = None
) -> tuple[Any, ScriptedTransport, FakeTime]:
    fake = fake or FakeTime()
    t = ScriptedTransport(*steps)
    result = spansh.scout(make_client(t, fake, cache_dir=cache_dir), kind, _query(kind), threading.Event())
    return result, t, fake


def test_request_shape_matches_what_spansh_accepted() -> None:
    for kind in ("riches", "bio"):
        rec = recorded(f"{kind}_submit")["request"]
        assert spansh.scout_form(kind, _query(kind)) == [tuple(p) for p in rec["form"]]
        assert f"{spansh.BASE}/{spansh.JOB_PATHS[kind]}" == rec["url"]


def test_riches_job_submits_polls_and_parses() -> None:
    result, t, fake = _run("riches", reply("riches_submit"), reply("riches_running"), reply("riches_done"))
    job = recorded("riches_submit")["body"]["job"]
    assert [r.method for r in t.requests] == ["POST", "GET", "GET"]
    assert t.requests[1].url == t.requests[2].url == f"{spansh.BASE}/results/{job}"
    assert fake.waits == [1.0, 1.0]  # polling at one per second
    assert result.kind == "riches" and not result.from_cache

    raw = recorded("riches_done")["body"]["result"]
    assert [s.id64 for s in result.systems] == [int(r["id64"]) for r in raw]
    assert result.systems[0].id64 == SOL and result.systems[-1].id64 == COLONIA
    assert result.systems[0].bodies == ()
    stop, raw_stop = result.systems[1], raw[1]
    assert (stop.name, stop.jumps, (stop.x, stop.y, stop.z)) == (
        raw_stop["name"],
        raw_stop["jumps"],
        (raw_stop["x"], raw_stop["y"], raw_stop["z"]),
    )
    for body, raw_body in zip(stop.bodies, raw_stop["bodies"], strict=True):
        assert str(body.id64) == raw_body["id64"]  # exact, even past 2^53
        assert body.scan_value == raw_body["estimated_scan_value"]
        assert body.mapping_value == raw_body["estimated_mapping_value"]
        assert body.distance_ls == raw_body["distance_to_arrival"]
        assert body.terraformable is raw_body["is_terraformable"]
        assert body.subtype == raw_body["subtype"]
        assert body.species == ()


def test_bio_job_sums_species_values() -> None:
    result, _, _ = _run("bio", reply("bio_submit"), reply("bio_done"))
    raw = recorded("bio_done")["body"]["result"]
    bodies = [b for s in result.systems for b in s.bodies]
    raw_bodies = [b for s in raw for b in s["bodies"]]
    assert len(bodies) == len(raw_bodies) > 0
    for body, raw_body in zip(bodies, raw_bodies, strict=True):
        assert [(s.genus, s.species, s.value, s.count) for s in body.species] == [
            (m["type"], m["subtype"], m["value"], m["count"]) for m in raw_body["landmarks"]
        ]
        assert body.bio_value == sum(m["value"] for m in raw_body["landmarks"])
        assert body.terraformable is False  # bio replies carry no terraformable flag
    assert max(b.bio_value for b in bodies) > 10_000_000


def test_completed_result_is_cached_by_query(tmp_path: Path) -> None:
    first, _, fake = _run("riches", reply("riches_submit"), reply("riches_done"), cache_dir=tmp_path)
    fake.now += 3600  # an hour later (and a new client, as after an EDMC restart)
    again, t2, _ = _run("riches", cache_dir=tmp_path, fake=fake)  # no scripted replies: any call would fail
    assert t2.requests == []
    assert again.from_cache and again.systems == first.systems
    # A different query is not served from the cache.
    other = ScriptedTransport(reply("riches_submit"), reply("riches_done"))
    q = _query("riches")
    spansh.scout(
        make_client(other, fake, cache_dir=tmp_path),
        "riches",
        ScoutQuery(q.from_system, q.to_system, q.range_ly + 1),
        threading.Event(),
    )
    assert len(other.requests) == 2


def test_unknown_system_is_an_http_error_with_spansh_message() -> None:
    with pytest.raises(HttpError) as err:
        _run("riches", reply("error_unknown_system"))
    assert err.value.status == 400
    assert "Could not find starting system" in str(err.value)


def test_unknown_job_is_an_http_error() -> None:
    with pytest.raises(HttpError) as err:
        _run("riches", reply("riches_submit"), reply("error_unknown_job"))
    assert err.value.status == 404


def test_a_job_spansh_refuses_fails_in_its_own_words() -> None:
    failed = Response(200, b'{"status": "error", "error": "Route too long"}')
    with pytest.raises(SourceError, match=r"^Route too long$"):
        _run("riches", reply("riches_submit"), failed)


def test_job_that_does_not_complete_is_a_bad_reply() -> None:
    with pytest.raises(BadReply, match="job did not complete"):
        _run("riches", reply("riches_submit"), Response(200, b'{"status": "error"}'))


def test_reply_missing_a_field_is_a_bad_reply() -> None:
    doc = recorded("riches_done")["body"]
    del doc["result"][1]["name"]
    with pytest.raises(BadReply, match="'name'"):
        _run("riches", reply("riches_submit"), Response(200, json.dumps(doc).encode()))


def test_job_that_never_finishes_hits_the_deadline() -> None:
    fake = FakeTime()
    running = reply("riches_running")
    t = ScriptedTransport(reply("riches_submit"), running, repeat_last=True)
    with pytest.raises(SourceError, match="did not finish"):
        spansh.scout(
            make_client(t, fake), "riches", _query("riches"), threading.Event(), deadline=30, clock=fake.clock
        )
    assert 30 <= len(t.requests) <= 33  # ≈ one poll per second until the deadline


def test_cancel_mid_poll_stops_polling() -> None:
    cancel = threading.Event()
    running = reply("riches_running")

    def poll_then_cancel(request: Request) -> Response:
        cancel.set()  # the user cancels while a poll is in flight
        return running

    t = ScriptedTransport(reply("riches_submit"), running, poll_then_cancel)
    with pytest.raises(Cancelled):
        spansh.scout(make_client(t, FakeTime()), "riches", _query("riches"), cancel)
    assert len(t.requests) == 3  # no poll after the cancel


def test_system_names_parse_exact_ids_and_skip_short_text(tmp_path: Path) -> None:
    fake = FakeTime()
    t = ScriptedTransport(reply("names"))
    client = make_client(t, fake, cache_dir=tmp_path)
    assert spansh.system_names(client, " C ", threading.Event()) == ()
    assert t.requests == []
    hits = spansh.system_names(client, "Col 285 Sector", threading.Event())
    raw = recorded("names")["body"]["min_max"]
    assert [(h.id64, h.name, h.x) for h in hits] == [(r["id64"], r["name"], r["x"]) for r in raw]
    assert t.requests[0].params == [("q", "Col 285 Sector")]
    # The same text again comes from the cache.
    assert spansh.system_names(client, "Col 285 Sector", threading.Event()) == hits
    assert len(t.requests) == 1


def test_the_nearest_system_spansh_knows_to_a_point() -> None:
    t = ScriptedTransport(reply("systems_nearest_merope"))
    hit = spansh.nearest_system(make_client(t, FakeTime()), (-75.0, -150.0, -340.0), threading.Event())
    assert (hit.id64, hit.name, hit.x) == (224644818084, "Merope", -78.59375)
    sent = json.loads(t.requests[0].json_body or "{}")
    assert sent == recorded("systems_nearest_merope")["request"]["json"]


def test_no_system_near_a_point_is_a_source_error() -> None:
    empty = Response(200, json.dumps({"count": 0, "results": []}).encode("utf-8"))
    with pytest.raises(SourceError):
        spansh.nearest_system(
            make_client(ScriptedTransport(empty), FakeTime()), (0.0, 0.0, 0.0), threading.Event()
        )
