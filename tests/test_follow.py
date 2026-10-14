"""Following a route: the next point moves on when you arrive in ANY later point (a skipped or overshot
stop is fine), never backwards; Skip / Back by hand; stop wealth in "$"; the route and its progress
survive a restart; a failed or cancelled search keeps the followed route. Journal replays drive it."""

import json
import math
import time
from collections.abc import Iterator
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest

from edmultitool.core.events import GameRoute, parse_nav_route
from edmultitool.core.follow import arrive, game_offer, game_route, points, start_index, step, ui_follow
from edmultitool.core.view import UiBody, UiRoute, UiStop, UiWaypoint, regrade, route_from_doc, wealth
from edmultitool.plugin import ScoutPlugin
from fakes import FakeTime, ScriptedTransport, make_client, reply
from test_scout_api import PREFS, _pump_until, _sample, _spansh

M = 1_000_000


def _stop(n: int, value: int = 0) -> UiStop:
    return UiStop(
        id64=str(100 + n), name=f"Stop {n}", leg_ly=10.0, leg_jumps=1, along_ly=10.0 * n, off_line_ly=1.0,
        scan_value=0, mapping_value=value, bio_value=0, wealth=wealth(value),
        bodies=(
            UiBody(f"{n}0", f"Stop {n} A", "A", "Earth-like world", 10.0, 0, value, False, True, 0, (),
                   wealth(value)),
        ),
    )  # fmt: skip


def _tour(n: int = 4) -> UiRoute:
    return regrade(UiRoute(
        from_name="Start", to_name="End", range_ly=20.0, total_ly=50.0, final_leg_ly=5.0, final_leg_jumps=1,
        from_cache=False, finished="2026-10-12T12:00:00+00:00",
        stops=tuple(_stop(i + 1, (i + 1) * 6 * M) for i in range(n)), from_id64="1", to_id64="2",
    ))  # fmt: skip


def _plot() -> UiRoute:
    names = ("Start", "N1", "N2", "End")
    return UiRoute(
        from_name="Start", to_name="End", range_ly=20.0, total_ly=150.0, final_leg_ly=0.0, final_leg_jumps=0,
        from_cache=False, finished="2026-10-12T12:00:00+00:00", stops=(), mode="neutron", range_basis="full",
        total_jumps=9,
        waypoints=tuple(
            UiWaypoint(str(200 + i), name, 50.0 if i else 0.0, (0, 4, 3, 2)[i], i in (1, 2), False, None)
            for i, name in enumerate(names)
        ),
        from_id64="200", to_id64="203",
    )  # fmt: skip


# --- core ------------------------------------------------------------------------------------------------
def test_points_are_start_stops_destination() -> None:
    assert points(_tour(2)) == ("1", "101", "102", "2")
    assert points(_plot()) == ("200", "201", "202", "203")


def test_new_route_starts_past_where_you_are_else_at_the_start() -> None:
    r = _tour()
    assert start_index(r, "1") == 1  # in the start system: the first stop is next
    assert start_index(r, "999") == 0  # elsewhere: fly to the start first
    assert start_index(r, None) == 0
    assert start_index(r, "102") == 3  # already at stop 2


def test_arrival_moves_past_any_later_point_never_back() -> None:
    r = _tour(4)  # points: start 1, stops 101..104, end 2
    n = arrive(r, 1, "102")  # skipped stop 1
    assert n == 3
    assert arrive(r, n, "104") == 5  # overshot stop 3
    assert arrive(r, n, "101") == n  # back in an earlier stop: nothing changes
    assert arrive(r, n, "555") == n  # off the route
    assert arrive(r, n, "2") == 6  # the destination: finished
    assert arrive(r, 6, "103") == 6


def test_skip_and_back_stay_within_the_route() -> None:
    r = _tour(2)
    assert step(r, 0, -1) == 0
    assert step(r, 3, 1) == 4 and step(r, 4, 1) == 4  # 4 = arrived
    assert step(r, 4, -1) == 3


@pytest.mark.parametrize(
    ("value", "dollars"),
    [
        (0, 0),
        (1, 1),
        (5 * M, 1),
        (5 * M + 1, 2),
        (10 * M, 2),
        (20 * M, 3),
        (20 * M + 1, 4),
        (50 * M, 4),
        (50 * M + 1, 5),
    ],
)
def test_wealth_steps(value: int, dollars: int) -> None:
    assert wealth(value) == dollars


def test_tour_progress_counts_what_is_ahead() -> None:
    r = _tour(4)  # stops worth 6M, 12M, 18M, 24M
    f = ui_follow(r, 2)
    assert (f.next_name, f.next_kind, f.next_wealth) == ("Stop 2", "stop", 3)
    assert (f.stops_left, f.value_ahead, f.ly_left) == (3, 54 * M, 35.0)
    assert f.text == "Stop 2 · stop 2/4 · $$$"
    start = ui_follow(r, 0)
    assert (start.next_name, start.next_kind, start.text) == ("Start", "start", "Start · start of the route")
    last = ui_follow(r, 5)
    assert (last.next_name, last.next_kind, last.stops_left, last.ly_left) == ("End", "destination", 0, 5.0)
    done = ui_follow(r, 6)
    assert done.finished and done.next_name == "" and done.text == "arrived at End"
    assert ui_follow(None, 0).text == "—" and not ui_follow(None, 0).active


def test_plot_progress_counts_jumps_left() -> None:
    r = _plot()
    f = ui_follow(r, 1)
    assert (f.next_name, f.next_kind, f.jumps_left) == ("N1", "waypoint", 9)
    assert f.text == "N1 · 1/3 · neutron · 9 jumps left"
    end = ui_follow(r, 3)
    assert (end.next_kind, end.jumps_left, end.text) == ("destination", 2, "End · destination · 2 jumps left")


def test_saved_route_reads_back_identical() -> None:
    from dataclasses import asdict

    for r in (_tour(), _plot()):
        assert route_from_doc(json.loads(json.dumps(asdict(r)))) == r
    with pytest.raises((ValueError, TypeError, KeyError)):
        route_from_doc({"stops": []})


# --- plugin: journal replay --------------------------------------------------------------------------------
def _jump(name: str, address: int | str) -> dict[str, Any]:
    return {
        "timestamp": "2026-10-12T12:00:00Z", "event": "FSDJump", "StarSystem": name,
        "SystemAddress": int(address), "StarPos": [1.0, 2.0, 3.0], "JumpDist": 10.0, "FuelUsed": 0.5,
        "FuelLevel": 20.0,
    }  # fmt: skip


@pytest.fixture
def scouted(tmp_path: Path) -> Iterator[ScoutPlugin]:
    """A plugin after a real (recorded) Sol → Colonia scout, the player still where the sample left off."""
    p = ScoutPlugin(tmp_path)
    for entry in _sample():
        p.on_journal(entry, None)
    p.client = make_client(ScriptedTransport(_spansh, repeat_last=True), FakeTime())
    p.start_worker()
    p.api_post("/api/scout", PREFS)
    _pump_until(p, "done")
    yield p
    p.stop_worker()


def _state(p: ScoutPlugin) -> dict[str, Any]:
    state: dict[str, Any] = json.loads(p.snapshot)["state"]
    return state


def test_replay_skip_overshoot_off_route_and_back(scouted: ScoutPlugin) -> None:
    p = scouted
    route = p.scout.route
    assert route is not None and len(route.stops) >= 6
    stops = route.stops
    f = _state(p)["follow"]
    assert (f["next_index"], f["next_kind"], f["next_name"]) == (0, "start", "Sol")  # not in Sol yet
    assert _state(p)["panel"]["next"] == "Sol · start of the route"

    p.on_journal(_jump("Sol", route.from_id64), None)
    assert _state(p)["follow"]["next_name"] == stops[0].name

    p.on_journal(_jump(stops[1].name, stops[1].id64), None)  # skipped stop 1
    f = _state(p)["follow"]
    assert (f["next_index"], f["next_name"]) == (3, stops[2].name)

    p.on_journal(_jump(stops[4].name, stops[4].id64), None)  # overshot stops 3 and 4
    assert _state(p)["follow"]["next_name"] == stops[5].name

    before = _state(p)["follow"]
    p.on_journal(_jump("Somewhere Else", 42), None)  # off the route
    p.on_journal(_jump(stops[0].name, stops[0].id64), None)  # back in an earlier stop
    assert _state(p)["follow"] == before

    f = before
    assert f["stops_left"] == len(stops) - 5
    assert f["value_ahead"] == sum(max(s.scan_value, s.mapping_value) + s.bio_value for s in stops[5:])
    assert _state(p)["panel"]["next"].startswith(f"{stops[5].name} · stop 6/{len(stops)} · $")
    dump = json.loads((p.data_dir / "debug" / "panel.json").read_text(encoding="utf-8"))
    assert (
        dump["follow"]["next_name"] == stops[5].name and dump["panel"]["next"] == _state(p)["panel"]["next"]
    )

    p.on_journal(_jump("Colonia", route.to_id64), None)
    f = _state(p)["follow"]
    assert f["finished"] and f["next_name"] == "" and p.next_name == ""
    assert _state(p)["panel"]["next"] == "arrived at Colonia"


def test_skip_and_back_from_the_page(scouted: ScoutPlugin) -> None:
    p = scouted
    assert p.api_post("/api/follow", {"step": 1}) == (202, {"accepted": "follow"})
    bad: object
    for bad in ({"step": 2}, {"step": "1"}, {"step": True}, {}, []):
        assert p.api_post("/api/follow", bad)[0] == 400  # type: ignore[index]
    handled = p.scout.handled
    p.pump()
    assert p.ui.follow.next_index == 1 and p.scout.handled == handled  # not a scout command
    p.api_post("/api/follow", {"step": 1})
    p.api_post("/api/follow", {"step": -1})
    p.pump()
    assert p.ui.follow.next_index == 1


def test_route_and_progress_survive_a_restart(scouted: ScoutPlugin, tmp_path: Path) -> None:
    p = scouted
    route = p.scout.route
    assert route is not None
    p.on_journal(_jump(route.stops[2].name, route.stops[2].id64), None)
    assert p.ui.follow.next_index == 4
    p.stop_worker()

    again = ScoutPlugin(tmp_path)  # EDMC restarted
    assert again.scout.route == route and again.ui.follow.next_index == 4
    assert again.ui.follow.next_name == route.stops[3].name and again.next_name == route.stops[3].name
    # Flown on while EDMC was closed: the first position catches the route up.
    again.on_journal(
        {"timestamp": "2026-10-12T13:00:00Z", "event": "Location", "StarSystem": route.stops[5].name,
         "SystemAddress": int(route.stops[5].id64), "StarPos": [0, 0, 0]},
        None,
    )  # fmt: skip
    assert again.ui.follow.next_index == 7


def test_bad_route_file_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    for text in (
        "{broken",
        '{"version": 99, "next": 0, "route": {}}',
        '{"version": 1, "next": 0, "route": 5}',
    ):
        (tmp_path / "data" / "route.json").write_text(text, encoding="utf-8")
        p = ScoutPlugin(tmp_path)
        assert p.scout.route is None and not p.ui.follow.active and p.ui.panel.next == "—"


def test_a_failed_search_keeps_the_followed_route(scouted: ScoutPlugin) -> None:
    p = scouted
    route = p.scout.route
    p.api_post("/api/follow", {"step": 1})
    p.pump()
    assert p.client is not None
    p.client = make_client(ScriptedTransport(reply("error_unknown_system"), repeat_last=True), FakeTime())
    p.api_post("/api/scout", PREFS)
    _pump_until(p, "error")
    assert p.scout.route == route and p.ui.follow.next_index == 1


def test_a_new_route_replaces_the_followed_one(scouted: ScoutPlugin) -> None:
    p = scouted
    p.api_post("/api/follow", {"step": 1})
    p.api_post("/api/follow", {"step": 1})
    p.pump()
    old = p.scout.route
    assert old is not None and p.ui.follow.next_index == 2
    handled = p.scout.handled
    p.api_post("/api/scout", PREFS)
    deadline = time.monotonic() + 5
    while (p.scout.handled == handled or p.scout.status != "done") and time.monotonic() < deadline:
        p.pump()
        time.sleep(0.01)
    assert p.scout.handled == handled + 1 and p.scout.status == "done"
    assert p.scout.route is not None and p.scout.route is not old  # a new object, built from the new reply
    assert p.ui.follow.next_index == 0  # the new route starts over
    saved = json.loads((p.data_dir / "route.json").read_text(encoding="utf-8"))
    assert saved["next"] == 0


def test_wealth_is_per_body_and_a_stop_takes_its_best_body(scouted: ScoutPlugin) -> None:
    """Dozens of small bodies must not outrank one planet full of species."""
    route = scouted.scout.route
    assert route is not None
    for s in route.stops:
        for b in s.bodies:
            worth = (max(b.scan_value, b.mapping_value) if b.scan_hit else 0) + b.bio_value
            assert b.wealth == wealth(worth)
        assert s.wealth == max(b.wealth for b in s.bodies)
    many_small = [wealth(4 * M)] * 12  # 48M in all, but each body 1 "$"
    assert max(many_small) == 1 and wealth(56 * M) == 5


def test_a_route_saved_without_body_wealth_is_graded_on_load(scouted: ScoutPlugin, tmp_path: Path) -> None:
    """A route saved before bodies had "$": it must load with today's grading."""
    route = scouted.scout.route
    assert route is not None
    scouted.stop_worker()
    path = tmp_path / "data" / "route.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    for s in doc["route"]["stops"]:
        s["wealth"] = 1
        for b in s["bodies"]:
            del b["wealth"]  # the older shape had no such field
    path.write_text(json.dumps(doc), encoding="utf-8")
    again = ScoutPlugin(tmp_path)
    assert again.scout.route == route
    assert all(b.wealth > 0 for s in again.scout.route.stops for b in s.bodies)


# --- the route plotted in the game's galaxy map -------------------------------------------------------------
NAVROUTE = json.loads((Path(__file__).parent / "data" / "navroute.json").read_text(encoding="utf-8"))


def _journal_with_navroute(tmp_path: Path, doc: dict[str, Any] | None = NAVROUTE) -> Path:
    folder = tmp_path / "journal"
    folder.mkdir()
    if doc is not None:
        (folder / "NavRoute.json").write_text(json.dumps(doc), encoding="utf-8")
    return folder


def test_navroute_file_is_parsed_from_a_real_plot() -> None:
    game = parse_nav_route(NAVROUTE)
    assert game is not None and len(game.points) == 7  # 6 jumps to 76 Sigma Ceti
    assert game.points[0].name == "Trianguli Sector EL-Y c7" and game.points[-1].name == "76 Sigma Ceti"
    assert parse_nav_route({"event": "NavRouteClear", "timestamp": "x"}) == GameRoute((), "x")
    assert parse_nav_route({"event": "NavRoute", "timestamp": "x"}) is None  # the journal stub
    assert parse_nav_route({"event": "NavRoute", "Route": [{"StarSystem": "A"}]}) is None
    route = game_route(game, 22.09, "2026-10-12T12:40:00+00:00")
    assert route.mode == "game" and route.total_jumps == 6 and len(route.waypoints) == 7
    assert route.waypoints[1].leg_ly == round(math.dist(game.points[0].pos, game.points[1].pos), 2)
    assert [w.scoopable for w in route.waypoints][:3] == [False, False, False]  # TTS, T, L: brown dwarfs
    assert route_from_doc(json.loads(json.dumps(asdict(route)))) == route


def test_offer_only_when_the_followed_route_does_not_lead_there() -> None:
    game = parse_nav_route(NAVROUTE)
    assert game is not None
    assert game_offer(game, None, 0).offer
    r = _tour(3)
    assert game_offer(game, r, 1).offer
    to_next_stop = GameRoute((game.points[0], replace(game.points[-1], address=102)), "t")
    assert not game_offer(to_next_stop, r, 2).offer  # plotting in the game to the next stop: no nagging
    assert game_offer(to_next_stop, r, 3).offer  # that stop is behind you now
    assert not game_offer(GameRoute((), "t"), r, 0).offer and not game_offer(None, r, 0).offer


def test_plugin_offers_and_follows_the_galaxy_map_route(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path, journal_dir=_journal_with_navroute(tmp_path))
    g = _state(p)["game_route"]  # read at start: plotted before EDMC started
    assert g == {"offer": True, "to_name": "76 Sigma Ceti", "jumps": 6, "plotted": NAVROUTE["timestamp"]}
    p.on_journal(
        {"timestamp": "2026-10-12T10:40:00Z", "event": "Location", "StarSystem": "Trianguli Sector EL-Y c7",
         "SystemAddress": 2008132063938, "StarPos": [93.21875, -38.375, -65.125]},
        None,
    )  # fmt: skip
    assert p.api_post("/api/follow", {"take": "game"}) == (202, {"accepted": "follow-game"})
    p.pump()
    s = _state(p)
    assert s["scout"]["route"]["mode"] == "game" and s["follow"]["next_index"] == 1  # you are at its start
    assert s["follow"]["next_name"] == "Trianguli Sector XU-O a6-1"
    assert s["follow"]["text"] == "Trianguli Sector XU-O a6-1 · 1/6 · 6 jumps left"
    assert not s["game_route"]["offer"]  # it is the followed route now

    p.on_journal({"timestamp": "2026-10-12T10:50:00Z", "event": "NavRouteClear"}, None)
    assert not _state(p)["game_route"]["offer"] and p.scout.route is not None  # following goes on


def test_journal_navroute_stub_reads_the_file_but_never_an_older_one(tmp_path: Path) -> None:
    folder = _journal_with_navroute(tmp_path, None)
    p = ScoutPlugin(tmp_path, journal_dir=folder)
    assert not _state(p)["game_route"]["offer"]
    (folder / "NavRoute.json").write_text(json.dumps(NAVROUTE), encoding="utf-8")
    p.on_journal({"timestamp": "2026-10-12T11:00:00Z", "event": "NavRoute"}, None)  # newer than the file
    assert not _state(p)["game_route"]["offer"]
    p.on_journal({"timestamp": NAVROUTE["timestamp"], "event": "NavRoute"}, None)
    assert _state(p)["game_route"]["offer"]
    # EDMC may hand over the route in the entry itself.
    p.on_journal({"event": "NavRouteClear", "timestamp": "2026-10-12T11:01:00Z"}, None)
    p.on_journal({**NAVROUTE, "timestamp": "2026-10-12T11:02:00Z"}, None)
    assert _state(p)["game_route"]["plotted"] == "2026-10-12T11:02:00Z"


def test_taking_without_a_plotted_route_does_nothing(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path)
    p.api_post("/api/follow", {"take": "game"})
    p.pump()
    assert p.scout.route is None
