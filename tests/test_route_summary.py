"""The route's totals, the prime planet filter (ELW hunt) and the remembered destinations."""

import json
from dataclasses import replace
from pathlib import Path

from edmultitool.core.explored import Explored
from edmultitool.core.route import MAX_POOL, build_route, scout_queries
from edmultitool.core.scout import BioSpecies, ScoutBody, ScoutKind, ScoutQuery, ScoutResult, ScoutSystem
from edmultitool.core.settings import (
    MAX_RECENT,
    ScoutPrefs,
    load_settings,
    parse_scout_prefs,
    remember_destination,
    save_settings,
)
from edmultitool.core.view import regrade, ui_route

START = ScoutSystem(1, "Start", 0, 0.0, 0.0, 0.0, ())
DEST = ScoutSystem(2, "Dest", 9, 100.0, 0.0, 0.0, ())
OFF = ScoutPrefs(to_system="Dest", min_scan=0, min_mapping=0, min_species=0, min_bio_body=0)


def body(
    id64: int, subtype: str, mapping: int, *, terraformable: bool = False, species: tuple[int, ...] = ()
) -> ScoutBody:
    return ScoutBody(
        id64, f"B {id64}", "Planet", subtype, 100.0, mapping // 3, mapping, terraformable,
        tuple(BioSpecies("Genus", f"Genus S{v}", v, 3) for v in species),
    )  # fmt: skip


def system(id64: int, x: float, *bodies: ScoutBody) -> ScoutSystem:
    return ScoutSystem(id64, f"S {id64}", 4, x, 0.0, 0.0, bodies)


def result(kind: ScoutKind, *systems: ScoutSystem) -> ScoutResult:
    return ScoutResult(kind, ScoutQuery("Start", "Dest", 20.0), (START, *systems, DEST))


PRIME = replace(OFF, planets="prime")


def test_prime_filter_keeps_earthlikes_water_ammonia_and_terraformables_only() -> None:
    riches = result(
        "riches",
        system(
            10, 20, body(101, "Earth-like world", 3_000_000), body(102, "High metal content world", 900_000)
        ),
        system(11, 40, body(111, "Water world", 1_000_000), body(112, "Ammonia world", 1_500_000)),
        system(12, 60, body(121, "High metal content world", 1_200_000, terraformable=True)),
        system(13, 80, body(131, "Metal rich body", 2_000_000)),  # valuable, but not prime: no stop
    )
    stops = build_route([riches], PRIME).stops
    assert {b.id64 for s in stops for b in s.bodies} == {101, 111, 112, 121}
    assert [s.id64 for s in stops] == [10, 11, 12]
    # "any" keeps them all
    assert {b.id64 for s in build_route([riches], OFF).stops for b in s.bodies} == {
        101,
        102,
        111,
        112,
        121,
        131,
    }


def test_prime_filter_leaves_the_bio_search_alone() -> None:
    bio = result("bio", system(20, 50, body(201, "Rocky body", 0, species=(5_000_000,))))
    (stop,) = build_route([bio], PRIME).stops
    assert stop.bio_value == 5_000_000


def test_prime_scan_search_asks_spansh_for_the_big_list() -> None:
    prefs = replace(PRIME, max_stops=7)
    (_, rq), (_, bq) = scout_queries(prefs, "Sol", 20.0)
    assert (rq.max_results, bq.max_results) == (MAX_POOL, 7)  # the bio search is not filtered: as asked
    (_, rq), _ = scout_queries(replace(prefs, planets="any"), "Sol", 20.0)
    assert rq.max_results == 7


def test_keep_takes_the_best_prime_stops_and_every_bio_stop() -> None:
    riches = result(
        "riches",
        system(10, 10, body(101, "Earth-like world", 1_000_000)),
        system(11, 20, body(111, "Earth-like world", 4_000_000)),
        system(12, 30, body(121, "Water world", 3_000_000)),
        system(13, 40, body(131, "Ammonia world", 2_000_000)),
    )
    bio = result("bio", system(14, 50, body(141, "Rocky body", 0, species=(9_000_000,))))
    kept = build_route([riches, bio], PRIME, keep=2).stops
    assert [s.id64 for s in kept] == [11, 12, 14]  # along the line again; the bio stop stays
    assert len(build_route([riches, bio], PRIME).stops) == 5  # no `keep` (replacement pool): all
    assert len(build_route([riches, bio], OFF, keep=2).stops) == 5  # "any": Spansh already gave N


def test_route_totals_are_what_the_stops_are_still_worth() -> None:
    riches = result(
        "riches",
        system(10, 20, body(101, "Earth-like world", 3_000_000), body(102, "Water world", 1_000_000)),
    )
    bio = result("bio", system(11, 50, body(111, "Rocky body", 0, species=(4_000_000, 2_000_000))))
    route = ui_route(build_route([riches, bio], OFF), "2026-10-12T12:00:00Z")
    assert (route.worth_map, route.worth_bio) == (4_000_000, 6_000_000)
    assert route.worth_scan == 3_000_000 // 3 + 1_000_000 // 3  # the test bodies scan for a third
    assert route.worth_map == sum(max(s.scan_value, s.mapping_value) for s in route.stops)
    # you map one and analyse a species: the totals drop by exactly that
    done = Explored(mapped=frozenset({101}), analysed=frozenset({(111, "genus s4000000")}))
    later = regrade(route, done)
    assert (later.worth_map, later.worth_bio) == (1_000_000, 2_000_000)


def test_planets_must_be_a_known_filter() -> None:
    from dataclasses import asdict

    form = asdict(ScoutPrefs(to_system="Colonia"))
    assert parse_scout_prefs({**form, "planets": "prime"}) == ScoutPrefs(to_system="Colonia", planets="prime")
    bad = parse_scout_prefs({**form, "planets": "gas giants"})
    assert isinstance(bad, str) and bad.startswith("'planets' must be one of")


def test_recent_destinations_newest_first_once_each_capped() -> None:
    recent: tuple[str, ...] = ()
    for name in ("Sol", "Colonia", "  sol ", "", "Achenar"):
        recent = remember_destination(recent, name)
    assert recent == ("Achenar", "sol", "Colonia")  # the newest spelling wins, blanks are ignored
    for i in range(20):
        recent = remember_destination(recent, f"System {i}")
    assert len(recent) == MAX_RECENT and recent[0] == "System 19"


def test_recent_destinations_survive_a_restart_and_bad_entries_are_dropped(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"recent_to": ["Colonia", 5, "x" * 101, "colonia", "Sol"]}), encoding="utf-8")
    got = load_settings(path)
    assert got.recent_to == ("Colonia", "Sol")
    save_settings(path, replace(got, recent_to=remember_destination(got.recent_to, "Achenar")))
    assert load_settings(path).recent_to == ("Achenar", "Colonia", "Sol")
    path.write_text(json.dumps({"recent_to": "Colonia"}), encoding="utf-8")
    assert load_settings(path).recent_to == ()


def test_keep_ranks_mapped_stops_too_so_the_detour_rule_still_decides() -> None:
    riches = result(
        "riches",
        system(10, 10, body(101, "Earth-like world", 5_000_000)),  # the best, but yours already
        system(11, 20, body(111, "Water world", 3_000_000)),
        system(12, 30, body(121, "Ammonia world", 2_000_000)),
    )
    done = Explored(mapped=frozenset({101}))
    kept = build_route([riches], PRIME, done, keep=2).stops
    assert [s.id64 for s in kept] == [10, 11]  # 10 stays (spent): swap_spent and its limit decide
    assert not kept[0].counted
