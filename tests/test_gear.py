"""Gear posts from the forum: decoding real posts, tying gear to places, rating, Spansh name searches, and
the page flow (paste → places → jumps) on recorded replies."""

# ruff: noqa: E501 — the expected decodings below are one line per real post

import json
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from edmultitool.core.gear import (
    GearItem,
    GearPlace,
    Lookup,
    ParsedPost,
    PlaceText,
    StationInfo,
    SystemInfo,
    locate,
    match_station,
    name_key,
    parse_fields,
    parse_post,
    rate,
    suits,
)
from edmultitool.core.gear_view import UiGear, ui_place, with_distances
from edmultitool.core.plot import PlotQuery, ShipBuild
from edmultitool.gear_lookup import lookup
from edmultitool.plugin import ScoutPlugin
from edmultitool.sources import spansh
from edmultitool.sources.http import HttpError, Request, Response
from fakes import FakeTime, ScriptedTransport, make_client, recorded, reply

DATA = Path(__file__).parent / "data"


def _posts() -> dict[str, str]:
    text = (DATA / "gear_posts.txt").read_text(encoding="utf-8")
    return {b.partition("\n")[0]: b.partition("\n")[2] for b in text.split("\n=== ")[1:]}


POSTS = _posts()


def _summary(p: ParsedPost) -> str:
    if p.reply:
        return "reply"
    out = []
    for s in p.segments:
        if isinstance(s, GearItem):
            mods = ",".join(s.mods) if s.mods else ("-" if s.empty else "?")
            out.append(f"{s.count}x G{s.grade} {s.name} [{mods}]{' taken' if s.taken else ''}")
        else:
            out.append("@ " + " | ".join(s.pieces))
    return " ; ".join(out) or "nothing"


# What every real post decodes to (read by hand against the post): "reply" = refused as not an offer,
# "@ a | b" = a line of place names, "Nx Gg item [mods]" = gear ("-" no mods, "?" mods not stated).
EXPECTED = {
    "p2267-1": "1x G2 Maverick [Improved Jump Assist] ; 1x G2 Manticore Intimidator [-] ; @ Shivis | Reiter Gateway",
    "p2267-2": "@ HARPULIDNA | Feoktistov Ring ; 1x G3 Dominator [Increased Air Reserves] ; 1x G3 Manticore Tormentor [Reload Speed]",
    "p2267-3": "reply",
    "p2267-4": "reply",
    "p2267-5": "@ INOVIK | Dias Ring ; 1x G2 Maverick [Extra Backpack Capacity] ; 1x G3 Manticore Intimidator [Audio Masking]",
    "p2266-1": "2x G3 Maverick [-] ; @ Katobriges | Paulo Da Gama Relay",
    "p2266-2": "1x G3 Karma AR-50 [Scope] ; @ 61 Cancri | Boorman Hub",
    "p2266-3": "@ Yer Tanri | Tshang Platform ; 1x G3 Dominator [-] ; 1x G2 Maverick [Improved Battery Capacity]",
    "p2266-4": "reply",
    "p2266-5": "reply",
    "p2266-6": "reply",
    "p2266-7": "@ HR 5791 | Robinson Dock",
    "p2266-8": "@ Kivas | Ross Stop ; 1x G2 Dominator [Extra Ammo Capacity]",
    "p2266-9": "@ Teaka | Piercy Terminal ; 1x G3 Artemis [?]",
    "p2266-10": "@ Col 285 Sector AS-F b13-1 | PaulmierdeGonneville Relay ; 1x G3 Artemis [Reduced Tool Battery Consumption] ; 1x G2 Dominator [Damage Resistance]",
    "p2266-11": "1x G3 Manticore Oppressor [Stability] ; @ Ocassi | Schrodinger's Base",
    "p2266-12": "1x G2 Artemis [Enhanced Tracking] ; 1x G3 TK Zenith [Noise Suppressor] ; @ Schumacher City | Wregoe JI-K d8-63 ; 1x G2 Maverick [Extra Backpack Capacity] ; @ Dynamo Constructions | Wregoe JI-K d8-63",
    "p2266-13": "@ 38 Leonis Minoris | Murphy City ; 1x G2 Maverick [Night Vision]",
    "p2266-14": "1x G3 Karma P-15 [Scope] ; @ Prunariu Reach | Synuefe TU-C b46-2",
    "p2266-15": "reply",
    "p2266-16": "@ Iksvati | Olsen Horizons ; 1x G3 Dominator [Night Vision]",
    "p2266-17": "1x G3 Maverick [Improved Jump Assist] ; @ Synuefe QQ-H b57-2 ; @ Morley Keep ; @ Hunter's Folly ; 1x G3 Karma P-15 [-]",
    "p2266-18": "@ Guttun | Grover terminal ; 1x G3 TK Aphelion [-] ; 1x G2 TK Zenith [-]",
    "p2266-19": "@ Banephtha | Hoyle Survey ; 1x G2 Manticore Tormentor [-]",
    "p2265-1": "reply",
    "p2265-2": "2x G3 Artemis [-] ; @ Pal Platform | PY Vulpeculae",
    "p2265-3": "@ Vafthruva ; @ Gentle Platform ; 1x G2 Dominator [Night Vision] taken",
    "p2265-4": "@ Turir | Brookes terminal ; 1x G3 Artemis [Faster Shield Regen] ; 1x G3 Artemis [-] ; 1x G3 TK Aphelion [Magazine Size]",
    "p2265-5": "1x G2 Maverick [Night Vision] ; 1x G3 Maverick [-] ; 1x G3 TK Zenith [-] ; 1x G2 TK Eclipse [-] ; @ Damnon | Staden Terminal",
    "p2265-6": "@ Yen Chelici | Wallace Hub ; 1x G3 Dominator [-]",
    "p2265-7": "reply",
    "p2265-8": "@ Marindhs | Meitner Holdings ; 1x G3 Dominator [Improved Battery Capacity]",
    "p2265-9": "@ LFT 698 ; @ Ivanishin Settlement ; 1x G2 Maverick [Improved Battery Capacity] ; @ Yuqui Bab ; @ Zajdel Platform ; 1x G3 Artemis [Reduced Tool Battery Consumption] ; @ Zajdel Platform ; 1x G2 Karma L-6 [-] ; @ Synuefe DL-V b35-0 ; @ Mayor Town ; 1x G3 Manticore Oppressor [-] ; @ Synuefe DQ-X c17-11 ; @ Paradisi Vision ; 1x G3 Manticore Intimidator [-] ; @ Ronhubbard Horizons ; 1x G3 Karma C-44 [Faster Handling] ; @ Guevara Legacy ; 1x G3 TK Zenith [Faster Handling] ; @ Ampere Enterprise ; 1x G3 Maverick [Damage Resistance,Increased Sprint Duration] ; @ Synuefe DQ-X c17-11 ; @ Sartre Assembly ; 1x G3 Karma C-44 [Faster Handling]",
    "p2265-10": "reply",
    "p2265-11": "reply",
    "p2265-12": "@ Nibelaako | Oramus Orbital ; 1x G3 Artemis [-] ; 1x G3 Karma P-15 [Stability]",
    "p2265-13": "@ Alacarakmo | Vlamingh Port ; 1x G3 Maverick [-]",
    "p2265-14": "@ Mech | Evangelisti Gateway ; 1x G3 Dominator [-] ; 1x G2 Karma L-6 [Stability]",
    "p2265-15": "1x G2 TK Aphelion [Magazine Size] ; @ Phra Gu | Hippalus Landing",
    "p2265-16": "1x G2 Dominator [Enhanced Tracking] ; @ HIP 58287 | Birmingham Depot",
    "p2265-17": "1x G3 TK Zenith [-] ; @ Dhata | Cleve Terminal",
    "p2265-18": "@ Ye'kua'gsa | Higginbotham Mines ; 1x G3 Maverick [-] ; 1x G3 Dominator [-]",
    "p2265-19": "reply",
    "p2265-20": "1x G2 Artemis [Increased Air Reserves] ; @ Baltek | Akers Works",
    "p2264-1": "@ Ayerdhal's Folly | Selk'nam ; 1x G3 Maverick [-] ; 1x G2 Maverick [Combat Movement Speed]",
    "p2264-2": "reply",
    "p2264-3": "@ Howard City | Nagnatae ; 1x G2 Maverick [Quieter Footsteps] ; 1x G2 Karma L-6 [?] ; 1x G2 Karma P-15 [?]",
    "p2264-4": "@ Serktomes | Dann Orbital ; 1x G3 Maverick [Damage Resistance]",
    "p2264-5": "@ Hahn Terminal | Keian Gu ; 1x G3 Maverick [Added Melee Damage,Reduced Tool Battery Consumption]",
    "p2264-6": "@ Galle Station | Koleti ; 1x G2 Dominator [-] ; 1x G3 Manticore Tormentor [-] ; 1x G2 Manticore Tormentor [Stability]",
    "p2264-7": "1x G3 Artemis [-] ; 1x G3 Maverick [-] ; 1x G2 Karma P-15 [-] ; @ Olela | Wisniewski-Snerg Landing",
    "p2264-8": "2x G2 TK Zenith [-] ; @ HIP 63440 | Piccard Outpost",
    "p2264-9": "@ Allowini | Chiao Landing ; 1x G3 Artemis [-] ; 1x G2 Dominator [Increased Sprint Duration] ; 1x G2 Manticore Tormentor [-] ; 1x G3 Manticore Executioner [-]",
    "p2264-10": "reply",
    "p2264-11": "reply",
    "p2264-12": "@ LHS 221 ; @ Schiltberger Port ; 1x G3 Karma P-15 [-] ; 1x G3 Dominator [-]",
    "p2264-13": "reply",
    "p2264-14": "reply",
    "p2264-15": "@ LHS 157 | Sheffield Sanctuary ; 1x G2 Maverick [Night Vision] ; 1x G2 Artemis [Faster Shield Regen] ; 1x G3 Manticore Tormentor [Stability,Stowed Reloading]",
    "p2264-16": "1x G2 Manticore Tormentor [Greater Range] ; @ Aleutes | Cunningham Landing",
    "p2264-17": "@ Col 359 Sector VM-P b8-3 ; @ North town ; 1x G2 Maverick [Combat Movement Speed] ; @ Nixon depot ; 1x G2 Maverick [Combat Movement Speed] ; 1x G2 TK Zenith [Stowed Reloading]",
    "p2264-18": "1x GNone Manticore Tormentor [?] taken ; 1x GNone Maverick [?] taken ; 1x GNone Artemis [?]",
    "p2264-19": "@ LHS 3317 | Smith Hub ; 1x G3 Dominator [Faster Shield Regen] ; 1x G3 Artemis [-] ; 1x G2 TK Aphelion [-]",
    "p2263-1": "@ Taman | Cavendish Installation ; 1x G3 Artemis [Enhanced Tracking] ; 1x G3 TK Eclipse [Audio Masking] ; @ Stheno | Heisenberg Platform ; 1x G3 Artemis [-] ; @ Shui Wei Sector JH-V c2-18 | Dawes Town ; 1x G2 Manticore Tormentor [-] ; @ Arietis Sector JS-S b4-4 | Yuyu Kankan ; 1x G3 Artemis [-] ; 1x G3 TK Zenith [-] ; @ Kuk | Perga City ; 1x G3 Dominator [-]",
    "p2263-2": "@ Col 285 Sector GD-Z c14-14 ; @ Crane Point ; 1x G3 Karma L-6 [-] ; @ Torngita ; @ Alas Terminal ; 1x G2 Artemis [Improved Battery Capacity] ; @ Largarayu ; @ Voss Terminal ; 1x G2 Dominator [Quieter Footsteps] ; @ Voss Terminal ; 1x G2 Maverick [Quieter Footsteps] ; @ Mills Enterprise ; 1x G2 Manticore Tormentor [Noise Suppressor] ; @ Chilton Point ; 1x G3 Karma P-15 [Audio Masking,Faster Handling] ; @ Wilcutt Refinery ; 1x G3 Dominator [Improved Jump Assist,Extra Backpack Capacity] ; @ Wilcutt Refinery ; 1x G3 TK Aphelion [Audio Masking] ; @ GaspardePortola Barracks ; 1x G3 Artemis [-]",
    "p2263-3": "@ HIP 58263 | Corben Platform ; 1x G2 Dominator [Added Melee Damage] ; 1x G2 Dominator [Increased Sprint Duration]",
    "p2263-4": "@ Col 285 Sector FO-D b27-0 ; @ Engel Industries ; 1x G3 Dominator [Night Vision] ; @ Engel Industries ; 1x G3 Karma P-15 [?] ; @ Stanton Lab ; 1x G3 Karma P-15 [Noise Suppressor] ; @ Frost Enterprise ; 1x G3 Manticore Executioner [?]",
    "p2263-5": "@ Col 285 Sector MG-O b22-2 | U.T.S.P DR | Helena Russel ; 1x G2 Artemis [Added Melee Damage]",
    "p2263-6": "@ Procyon | Davy Dock ; 1x G3 Maverick [-] ; 1x G2 Dominator [-]",
    "p2263-7": "@ KLAMATH ; @ Thoreau Orbital ; 1x G3 Dominator [Increased Air Reserves] ; 1x G3 Maverick [Night Vision] ; 1x G2 Karma AR-50 [Scope] ; @ Rozhdestvensky City ; 1x G3 Dominator [Improved Jump Assist]",
    "p2263-8": "reply",
    "p2263-9": "@ Ross 104 | Shiner Port ; 1x G3 Artemis [Improved Jump Assist]",
    "p2263-10": "@ Crucis Sector MN-K a8-2 | Maunder Depot ; 1x G3 Manticore Tormentor [Improved Hip Fire Accuracy]",
    "p2263-11": "reply",
    "p2263-12": "@ HIP 68154 | Becat Refuge ; 1x G3 Maverick [Increased Air Reserves] ; 1x G2 TK Eclipse [Noise Suppressor]",
    "p2263-13": "@ Chamuluma | Bisnovatyi-Kogan Landing ; 2x G3 Dominator [-]",
    "p2263-14": "@ CM Draconis | Frimout Horizons ; 1x G2 Manticore Executioner [Magazine Size]",
    "p2263-15": "1x G3 TK Aphelion [Faster Handling] ; @ Groombridge 1618 | Gernsback Terminal",
    "p2263-16": "@ Bartlett City | Scorpio Sector JH-V B2-5 ; 1x G2 Manticore Tormentor [-] ; 1x G2 Maverick [-]",
    "p2263-17": "@ Hyades Sector AL-X c1-2 ; @ Daedalus Station ; 1x G2 Artemis [Night Vision] ; 1x G3 Artemis [Improved Jump Assist]",
    "p2263-18": "@ Turir | Brooks Terminal ; 1x G2 Artemis [Increased Sprint Duration] ; 1x G2 Dominator [-] ; 1x G2 TK Zenith [-]",
    "p2263-19": "@ Maris | Reeves Installation ; 1x G3 Maverick [-] ; 1x G3 Karma P-15 [-]",
    "p2263-20": "@ Walters Port | Condovichs ; 1x G3 Maverick [Extra Backpack Capacity]",
    "p2262-1": "@ LHS 317 | Northrop Port ; 1x G2 Dominator [Quieter Footsteps] ; 1x G3 TK Zenith [Headshot Damage]",
    "p2262-2": "@ Col 359 Sector VM-P b8-3 ; @ North town ; 1x G3 Artemis [Extra Ammo Capacity] ; @ Nixon depot ; 1x G3 P12 [Scope] ; 1x G3 TK Zenith [-] ; @ Kirk town ; 1x G3 Karma L-6 [-] ; @ Bruck depot ; 1x G3 Maverick [Enhanced Tracking] ; 1x G3 Karma L-6 [Noise Suppressor]",
    "p2262-3": "@ Ra | Lehtonen Hub ; 1x G2 Dominator [Faster Shield Regen] ; 1x G3 TK Aphelion [Stowed Reloading]",
    "p2262-4": "reply",
    "p2262-5": "1x G3 P12 [?] taken ; 1x G3 Maverick [Enhanced Tracking] ; 1x G3 Karma L-6 [Noise Suppressor] taken",
    "p2262-6": "reply",
    "p2262-7": "@ Yoru | Manning Base ; 1x G3 Artemis [Improved Battery Capacity]",
    "p2262-8": "@ Hip 108158 | Alexeyev Terminal ; 1x G3 TK Zenith [Greater Range]",
    "p2262-9": "@ LHS 5333 | Satcher City ; 1x G3 Maverick [Quieter Footsteps] taken ; @ LHS 5333 | Hooke Orbital ; 1x G3 Maverick [?] ; @ LHS 5333 | Boe Orbital ; 1x G2 Dominator [?] ; @ LHS 5333 | Boe Orbital ; 1x G2 Karma P-15 [?]",
    "p2262-10": "@ Col 285 Sector KY-H C10-10 | Kornbluth Platform ; 1x G3 Manticore Intimidator [?]",
    "p2262-11": "reply",
    "p2262-12": "1x G3 Dominator [Faster Shield Regen] ; @ 3 Corvi | Lichternberg Dock",
    "p2262-13": "reply",
    "p2262-14": "reply",
    "p2262-15": "reply",
    "p2262-16": "@ Piorimudjar ; @ Chacornac Orbital ; 1x G2 Maverick [Reduced Tool Battery Consumption] ; @ Asami Market ; 1x G2 Manticore Tormentor [-] ; @ Gunter Terminal ; 1x G3 Maverick [-] ; @ Sagan Terminal ; 1x G3 Dominator [Increased Air Reserves] ; @ Dimor ; @ Aoki Terminal ; 1x G2 Dominator [-] ; @ CD-63 201 ; @ Szentmartony City ; 1x G2 Artemis [Increased Sprint Duration]",
    "p2262-17": "1x G2 Karma L-6 [Faster Handling] ; @ COL 285 SECTOR AC-H B12-1 | COLET'S PROGRESS",
    "p2262-18": "@ MARICORIANG ; @ Sekowski Dock ; 1x G3 Maverick [Quieter Footsteps] ; @ Jenkinson Port ; 1x G2 Maverick [Faster Shield Regen] ; 1x G2 TK Zenith [Magazine Size] ; @ KUUKESE ; @ Wingrove Port ; 1x G3 Maverick [-] ; 1x G2 Dominator [Improved Battery Capacity] ; @ TATAMO ; @ Plexico Orbital ; 1x G2 Artemis [Combat Movement Speed] ; 1x G3 Dominator [Increased Air Reserves] ; 1x G3 Karma C-44 [Stowed Reloading] ; 1x G2 Karma P-15 [Audio Masking]",
    "p2262-19": "@ Murato | Tietjen Ring ; 1x G3 Artemis [-]",
}


def test_every_real_post_decodes_as_read_by_hand() -> None:
    assert set(EXPECTED) == set(POSTS)
    got = {k: _summary(parse_post(text)) for k, text in POSTS.items()}
    assert {k: v for k, v in got.items() if v != EXPECTED[k]} == {}


def test_most_real_posts_are_offers_and_the_rest_replies() -> None:
    offers = [k for k, v in EXPECTED.items() if v != "reply" and "@" in v]
    assert len(offers) >= 70
    assert sum(v == "reply" for v in EXPECTED.values()) >= 20


@pytest.mark.parametrize(
    ("text", "price"),
    [
        (
            "G3 | Mav Suit | 2 mods Dmg Resist + Inc Sprint Duration | 18.750m CR | Ampere Enterprise",
            18_750_000,
        ),
        ("G3 Artemis + enhanced Tracking 11 250 000 Cr", 11_250_000),
        ("G2 | Karma L-6 | 0 mods | 875K CR | Zajdel Platform", 875_000),
        ("G2 Manticore Tormentor, no mods - 250,000", 250_000),
        ("G3 Maverick, 3,750,000 Credits @ LHS 5333 System, Hooke Orbital (Large Pad)", 3_750_000),
    ],
)
def test_prices_in_every_spelling(text: str, price: int) -> None:
    (item,) = parse_post(text).items
    assert item.price == price


def test_glued_grade_and_a_mod_on_its_own_line() -> None:
    (item,) = parse_post("Dom G3\nfaster shield regen\n3 Corvi, Lichternberg Dock").items
    assert (item.name, item.grade, item.mods) == ("Dominator", 3, ("Faster Shield Regen",))
    (glued,) = parse_post("G3Maverick suit + Increased O2").items
    assert (glued.name, glued.grade, glued.mods) == ("Maverick", 3, ("Increased Air Reserves",))


def test_a_suit_mod_never_lands_on_a_weapon() -> None:
    (item,) = parse_post("G3 Karma P-15 - Night Vision, Stability").items
    assert item.mods == ("Stability",)


# --- tying gear to places -------------------------------------------------------------------------
def _station(name: str, system: SystemInfo | None = None, sid: int = 1) -> StationInfo:
    home_id, home = (system.id64, system.name) if system else (sid, "?")
    return StationInfo(name, "Outpost", "M", 100.0, False, home_id, home)


def _system(name: str, id64: int, *stations: str) -> SystemInfo:
    base = SystemInfo(id64, name, float(id64), 0.0, 0.0)
    return SystemInfo(id64, name, float(id64), 0.0, 0.0, tuple(_station(s, base) for s in stations))


def _lookup(*systems: SystemInfo, stations: dict[str, tuple[StationInfo, ...]] | None = None) -> Lookup:
    by_name = stations or {}
    homes = {
        s.system_id64: SystemInfo(s.system_id64, s.system_name, 0, 0, 0, (s,))
        for hits in by_name.values()
        for s in hits
    }
    return Lookup({name_key(s.name): s for s in systems}, {name_key(k): v for k, v in by_name.items()}, homes)


def _where(places: tuple[GearPlace, ...]) -> list[tuple[str, str, list[str]]]:
    return [
        (p.system.name if p.system else "", p.station.name if p.station else "", [i.name for i in p.items])
        for p in places
    ]


def test_gear_before_its_place_goes_with_the_place_after_it() -> None:
    wregoe = _system("Wregoe JI-K d8-63", 7, "Dynamo Constructions")
    places = locate(parse_post(POSTS["p2266-12"]), _lookup(wregoe))
    assert _where(places) == [
        ("Wregoe JI-K d8-63", "", ["Artemis", "TK Zenith"]),
        ("Wregoe JI-K d8-63", "Dynamo Constructions", ["Maverick"]),
    ]
    assert places[0].note == "Schumacher City is not listed at Spansh"


def test_system_line_then_station_line_is_one_place() -> None:
    synuefe = _system("Synuefe QQ-H b57-2", 9, "Morley Keep", "Hunter's Folly")
    assert _where(locate(parse_post(POSTS["p2266-17"]), _lookup(synuefe))) == [
        ("Synuefe QQ-H b57-2", "Morley Keep", ["Maverick"]),
        ("Synuefe QQ-H b57-2", "Hunter's Folly", ["Karma P-15"]),
    ]


def test_a_list_post_gives_every_station_its_own_gear() -> None:
    systems = (
        _system("Maricoriang", 1, "Sekowski Dock", "Jenkinson Port"),
        _system("Kuukese", 2, "Wingrove Port"),
        _system("Tatamo", 3, "Plexico Orbital"),
    )
    places = locate(parse_post(POSTS["p2262-18"]), _lookup(*systems))
    assert [(s, st, len(items)) for s, st, items in _where(places)] == [
        ("Maricoriang", "Sekowski Dock", 1),
        ("Maricoriang", "Jenkinson Port", 2),
        ("Kuukese", "Wingrove Port", 2),
        ("Tatamo", "Plexico Orbital", 4),
    ]


def test_station_named_first_and_a_typo_still_match() -> None:
    keian = _system("Keian Gu", 4, "Hahn Terminal")
    assert _where(locate(parse_post(POSTS["p2264-5"]), _lookup(keian))) == [
        ("Keian Gu", "Hahn Terminal", ["Maverick"])
    ]
    turir = _system("Turir", 5, "Brooks Terminal", "Bachman Hub")
    assert _where(locate(parse_post(POSTS["p2265-4"]), _lookup(turir)))[0][1] == "Brooks Terminal"
    assert match_station("PaulmierdeGonneville Relay", (_station("Paulmier de Gonneville Relay"),))
    assert (
        match_station("Brooks Terminal", (_station("Brookes Terminal"), _station("Brooks Terminals"))) is None
    )


def test_a_station_known_by_name_only_brings_its_system() -> None:
    hits = (_station("Walters Port", SystemInfo(11, "Condovichs", 1, 2, 3)),)
    places = locate(parse_post(POSTS["p2263-20"]), _lookup(stations={"Walters Port": hits}))
    assert _where(places) == [("Condovichs", "Walters Port", ["Maverick"])]
    many = (_station("Hahn Terminal", sid=1), _station("Hahn Terminal", sid=2))
    (place,) = locate(parse_post("Hahn Terminal\nG3 Maverick"), _lookup(stations={"Hahn Terminal": many}))
    assert place.system is None and place.note == "2 stations are called Hahn Terminal; add the system"


def test_unknown_place_is_shown_with_its_gear() -> None:
    (place,) = locate(parse_post("Nowhere Qq - Nada Port\nG3 Maverick - Night Vision"), _lookup())
    assert place.system is None and place.note == "not found" and len(place.items) == 1


def test_fields_are_one_place_and_its_gear() -> None:
    post = parse_fields("Shivis", "Reiter Gateway", "G3 Dominator - Night Vision\nG2 Mav")
    assert post.pieces == ("Shivis", "Reiter Gateway")
    assert [i.name for i in post.items] == ["Dominator", "Maverick"]
    assert _where(locate(post, _lookup(_system("Shivis", 3, "Reiter Gateway")))) == [
        ("Shivis", "Reiter Gateway", ["Dominator", "Maverick"])
    ]


def test_distance_follows_you() -> None:
    (place,) = locate(parse_post("Shivis\nG3 Mav"), _lookup(_system("Shivis", 3)))
    gear = UiGear(places=(ui_place(place),))
    assert with_distances(gear, (0.0, 4.0, 0.0)).places[0].distance_ly == 5.0
    assert with_distances(gear, None).places[0].distance_ly is None


def test_parsing_keeps_place_lines_in_order() -> None:
    p = parse_post(POSTS["p2262-9"])
    lines = [s.line for s in p.segments if isinstance(s, PlaceText)]
    assert lines == [0, 1, 2, 3]


# --- rating -----------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("item", "stars"),
    [
        (GearItem("Maverick", "suit", 3, ("Damage Resistance", "Increased Sprint Duration")), 3),
        (GearItem("Dominator", "suit", 2, ("Night Vision",)), 3),
        (GearItem("Karma P-15", "weapon", 3, ("Audio Masking", "Faster Handling")), 3),
        (GearItem("Artemis", "suit", 3, (), empty=True), 3),
        (GearItem("Karma L-6", "weapon", 2, ("Stability",)), 1),
        (GearItem("Dominator", "suit", 3, ("Damage Resistance", "Faster Shield Regen")), 5),
        (GearItem("Maverick", "suit", 3, ("Night Vision", "Extra Backpack Capacity")), 5),
        (GearItem("Manticore Tormentor", "weapon", 3, ("Audio Masking", "Noise Suppressor")), 5),
        (GearItem("Dominator", "suit", 3, ("Added Melee Damage", "Increased Air Reserves")), 2),
        (GearItem("Manticore Oppressor", "weapon", 3, (), empty=True), 1),
        (GearItem("Dominator", "suit", 2, (), empty=True), 2),
    ],
)
def test_rating_matches_the_researched_examples(item: GearItem, stars: int) -> None:
    assert rate(item)[0] == stars


def test_rating_says_why_and_refuses_what_it_does_not_know() -> None:
    assert rate(GearItem("Dominator", "suit", 2, ("Night Vision",))) == (
        3,
        "G2 Dominator: Night Vision top, rare",
    )
    assert rate(GearItem("P12", "", 3)) == (0, "item not known")
    assert rate(GearItem("Maverick", "suit", None)) == (0, "grade not given")


def test_a_place_is_rated_by_its_best_item_still_there() -> None:
    items = (
        GearItem("Dominator", "suit", 3, ("Damage Resistance", "Faster Shield Regen"), taken=True),
        GearItem("Artemis", "suit", 3, (), empty=True),
    )
    place = ui_place(GearPlace(_system("Shivis", 3), None, items, "Shivis"))
    assert [i.stars for i in place.items] == [5, 3]
    assert place.stars == 3


# --- Spansh name searches -----------------------------------------------------------------------------
def test_system_search_sends_the_names_and_reads_the_stations() -> None:
    doc = recorded("gear_systems_keian")
    t = ScriptedTransport(reply("gear_systems_keian"))
    (keian,) = spansh.systems_by_name(make_client(t, FakeTime()), ["Keian Gu"], threading.Event())
    sent = t.requests[0]
    assert sent.method == "POST" and sent.url.endswith("/systems/search")
    assert sent.headers["Content-Type"] == "application/json"
    assert json.loads(sent.json_body or "") == doc["request"]["json"]
    assert keian.name == "Keian Gu" and keian.id64 == doc["body"]["results"][0]["id64"]
    hahn = next(s for s in keian.stations if s.name == "Hahn Terminal")
    assert (hahn.pad, hahn.system_name) == ("M", "Keian Gu")


def test_station_search_brings_each_station_with_its_system() -> None:
    t = ScriptedTransport(reply("gear_stations_walters"))
    rows = spansh.stations_by_name(make_client(t, FakeTime()), ["Walters Port"], threading.Event())
    assert [(st.name, sys.name) for st, sys in rows] == [("Walters Port", "Condovichs")]
    assert rows[0][1].id64 == rows[0][0].system_id64


def test_searches_are_cached(tmp_path: Path) -> None:
    t = ScriptedTransport(reply("gear_systems_keian"))
    client = make_client(t, FakeTime(), cache_dir=tmp_path)
    first = spansh.systems_by_name(client, ["Keian Gu"], threading.Event())
    again = spansh.systems_by_name(client, ["Keian Gu"], threading.Event())
    assert first == again
    assert len(t.requests) == 1


def test_lookup_asks_the_autocomplete_only_for_names_that_may_be_systems() -> None:
    t = ScriptedTransport(reply("gear_names_keian"), reply("gear_systems_keian"))
    found = lookup(make_client(t, FakeTime()), parse_post(POSTS["p2264-5"]), threading.Event())
    asked = [dict(r.params).get("q") for r in t.requests if r.method == "GET"]
    assert asked == ["Keian Gu"]  # "Hahn Terminal" reads like a station: never asked for as a system
    assert [s.name for s in found.systems.values()] == ["Keian Gu"]


# --- the page flow ------------------------------------------------------------------------------------
def _sample() -> list[dict[str, Any]]:
    lines = (DATA / "journal_sample.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _spansh(request: Request) -> Response:
    if "/field_values/system_names" in request.url:
        return reply("gear_names_keian")
    if request.url.endswith("/systems/search"):
        return reply("gear_systems_keian")
    if request.url.endswith("/generic/route"):
        return reply("exact_submit")
    return reply("exact_done")


@pytest.fixture
def plugin(tmp_path: Path) -> Iterator[tuple[ScoutPlugin, ScriptedTransport]]:
    p = ScoutPlugin(tmp_path)
    for entry in _sample():
        p.on_journal(entry, None)
    t = ScriptedTransport(_spansh, repeat_last=True)
    p.client = make_client(t, FakeTime())
    p.start_worker()
    yield p, t
    p.stop_worker()


def _pump_until(p: ScoutPlugin, done: Callable[[], bool], timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while not done() and time.monotonic() < deadline:
        p.pump()
        time.sleep(0.01)
    assert done(), p.gear


def test_paste_finds_the_place_rates_the_gear_and_counts_jumps(
    plugin: tuple[ScoutPlugin, ScriptedTransport],
) -> None:
    p, _t = plugin
    assert p.api_post("/api/gear", {"text": POSTS["p2264-5"]}) == (202, {"accepted": "gear"})
    _pump_until(p, lambda: p.gear.jumps_status == "done")
    gear = p.ui.gear
    assert gear.status == "done" and gear.message == "1 place, 1 item." and gear.handled == 1
    (place,) = gear.places
    assert (place.system, place.station, place.pad) == ("Keian Gu", "Hahn Terminal", "M")
    assert place.distance_ly is not None and place.distance_ly > 0
    exact = spansh._waypoints(recorded("exact_done")["body"]["result"], False)
    assert place.jumps == sum(w.jumps for w in exact)
    assert gear.jumps_from == p.state.system
    (item,) = place.items
    assert (item.name, item.grade, item.stars) == (
        "Maverick",
        3,
        3,
    )  # 2 + 0.5 suit - 0.5 melee + 0.5 tool battery
    snapshot = json.loads(p.snapshot)["state"]["gear"]
    assert snapshot["places"][0]["items"][0]["why"].startswith("G3 Maverick: Added Melee Damage dead")


def test_a_reply_is_refused_without_asking_spansh(plugin: tuple[ScoutPlugin, ScriptedTransport]) -> None:
    p, t = plugin
    p.api_post("/api/gear", {"text": POSTS["p2267-3"]})
    p.pump()
    assert p.gear.status == "error" and p.gear.message.startswith("This reads like a reply")
    assert not p.gear.fields.show
    assert not [r for r in t.requests if "search" in r.url or "system_names" in r.url]


def test_no_place_opens_the_fields_prefilled(plugin: tuple[ScoutPlugin, ScriptedTransport]) -> None:
    p, _t = plugin
    p.api_post("/api/gear", {"text": "G3 Dominator - Night Vision\nG2 Mav"})
    p.pump()
    assert p.gear.status == "error" and p.gear.fields.show
    assert p.gear.fields.gear == "G3 Dominator - Night Vision\nG2 Maverick"
    p.api_post("/api/gear", {"system": "Keian Gu", "station": "Hahn Terminal", "gear": p.gear.fields.gear})
    _pump_until(p, lambda: p.gear.status == "done")
    (place,) = p.gear.places
    assert place.station == "Hahn Terminal" and len(place.items) == 2
    p.api_post("/api/gear", {"clear": True})
    p.pump()
    cleared = p.gear
    assert cleared.places == () and cleared.status == "idle" and cleared.handled == 3


@pytest.mark.parametrize(
    ("doc", "error"),
    [
        ([], "send a JSON object"),
        ({"text": "  "}, "paste a post first"),
        ({"text": 5}, "paste a post first"),
        ({"system": "", "station": ""}, "type a system or a station"),
        ({"system": 1}, "system, station and gear must be text"),
    ],
)
def test_bad_gear_requests_are_refused(tmp_path: Path, doc: object, error: str) -> None:
    p = ScoutPlugin(tmp_path)
    assert p.api_post("/api/gear", doc) == (400, {"error": error})
    assert p._commands.empty()


def test_a_shared_station_name_goes_to_the_system_spelled_like_the_post() -> None:
    hits = (
        _station("Bartlett City", SystemInfo(1, "Scorpii Sector JH-V b2-5", 0, 0, 0)),
        _station("Bartlett City", SystemInfo(2, "Wolf 1301", 0, 0, 0)),
    )
    (place,) = locate(parse_post(POSTS["p2263-16"]), _lookup(stations={"Bartlett City": hits}))
    assert place.system is not None and place.system.name == "Scorpii Sector JH-V b2-5"
    assert place.note == "taken as Scorpii Sector JH-V b2-5"


def test_a_search_is_retried_after_a_bad_gateway_a_job_submit_is_not() -> None:
    fake = FakeTime()
    t = ScriptedTransport(Response(502, b""), reply("gear_systems_keian"))
    (keian,) = spansh.systems_by_name(make_client(t, fake), ["Keian Gu"], threading.Event())
    assert keian.name == "Keian Gu" and len(t.requests) == 2 and fake.waits[-1] == 2.0
    submit = ScriptedTransport(Response(502, b"{}"))
    with pytest.raises(HttpError):
        spansh.plot(make_client(submit, FakeTime()), _plot_query(), threading.Event())
    assert len(submit.requests) == 1


def _plot_query() -> PlotQuery:
    ship = ShipBuild(1175, 5.2, 0.013, 2.45, 573.5, 32, 0.75, 0, 4)
    return PlotQuery(1, 2, ship, 0)


@pytest.mark.parametrize(
    ("text", "reply", "pieces"),
    [
        ("G3 Dominator - Night Vision (taken)\nG2 Maverick - Stability\nShivis - Reiter Gateway", False, 2),
        ("Got a few to share today\nShivis - Reiter Gateway\nG3 Dominator - Night Vision", False, 2),
        ("Got the tourmentor, thanks", True, 0),
        ("Synuefe XR-H c44-3 - Pinto Port\nG3 Dominator - Night Vision", False, 2),  # c44 is a mass code
        ("G2 Maverick - Stability\nShivis", False, 1),  # a weapon mod on a suit is no place name
    ],
)
def test_offers_that_look_like_replies_and_names_that_look_like_gear(
    text: str, reply: bool, pieces: int
) -> None:
    post = parse_post(text)
    assert post.reply is reply
    assert len(post.pieces) == pieces
    assert all(i.name != "Karma C-44" for i in post.items)


def test_sold_here_is_not_gone() -> None:
    (item,) = parse_post("G3 Maverick sold here, 11.25m cr\nShivis").items
    assert not item.taken and item.price == 11_250_000


def test_mods_not_stated_are_rated_as_free_slots_and_say_so() -> None:
    assert rate(GearItem("Artemis", "suit", 3)) == (3, "G3 Artemis: mods not stated: rated as 2 free slots")


def test_a_refused_post_drops_the_lookup_still_running(tmp_path: Path) -> None:
    release = threading.Event()

    def slow(request: Request) -> Response:
        release.wait(5)
        return _spansh(request)

    p = ScoutPlugin(tmp_path)
    for entry in _sample():
        p.on_journal(entry, None)
    p.client = make_client(ScriptedTransport(slow, repeat_last=True), FakeTime())
    p.start_worker()
    try:
        p.api_post("/api/gear", {"text": POSTS["p2264-5"]})
        p.pump()
        assert p.gear.status == "running"
        p.api_post("/api/gear", {"text": POSTS["p2267-3"]})
        p.pump()
        release.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            p.pump()
            time.sleep(0.01)
        refused = p.gear
        assert refused.status == "error" and refused.places == () and refused.handled == 2
    finally:
        release.set()
        p.stop_worker()


def test_jumps_wait_for_a_position_then_are_counted(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path)
    p.client = make_client(ScriptedTransport(_spansh, repeat_last=True), FakeTime())
    p.start_worker()
    try:
        p.api_post("/api/gear", {"text": POSTS["p2264-5"]})
        _pump_until(p, lambda: p.gear.status == "done")
        assert (
            p.gear.places[0].jumps_note
            == "jumps not counted: jump once in the game so it knows where you are"
        )
        for entry in _sample():
            p.on_journal(entry, None)
        _pump_until(p, lambda: p.gear.jumps_status == "done")
        assert p.gear.places[0].jumps is not None
    finally:
        p.stop_worker()


@pytest.mark.parametrize(
    ("text", "reply", "pieces"),
    [
        ("Stock for this week at Shivis, Reiter Gateway:\nG3 Dominator NV", False, 2),
        ("Restocked today: Shivis - Reiter Gateway\nG3 Dom - NV", False, 2),
        (
            "Thanks for all the shares, here are mine\nShivis - Reiter Gateway\nG3 Dominator - Night Vision",
            False,
            2,
        ),
        ("Thank you all. Shivis - Reiter Gateway has G3 Dom NV", False, 2),
        ("G3 Dominator NV - taken\nG3 Artemis | Shivis | Reiter Gateway", False, 2),
        ("Many thanks!\nI purchased the below:\nShivis\nG3 | Mav Suit | 0 mods | Reiter Gateway", True, 2),
    ],
)
def test_polite_openers_and_dated_lines_stay_offers(text: str, reply: bool, pieces: int) -> None:
    post = parse_post(text)
    assert (post.reply, len(post.pieces)) == (reply, pieces)


def test_a_refused_reply_that_names_gear_and_a_place_offers_the_fields(
    plugin: tuple[ScoutPlugin, ScriptedTransport],
) -> None:
    p, _t = plugin
    p.api_post(
        "/api/gear", {"text": "Many thanks!\nI purchased the below:\nShivis - Reiter Gateway\nG3 Dom NV"}
    )
    p.pump()
    assert p.gear.status == "error" and p.gear.message.endswith("If it is an offer, check the fields below.")
    assert (p.gear.fields.show, p.gear.fields.system, p.gear.fields.station) == (
        True,
        "Shivis",
        "Reiter Gateway",
    )


def test_jumps_remember_where_they_were_counted_from(plugin: tuple[ScoutPlugin, ScriptedTransport]) -> None:
    p, _t = plugin
    p.api_post("/api/gear", {"text": POSTS["p2264-5"]})
    _pump_until(p, lambda: p.gear.jumps_status == "done")
    assert p.ui.gear.jumps_from_id64 == p.ui.address


@pytest.mark.parametrize(
    "text",
    [
        "Thanks! Got the Dom at Shivis - Reiter Gateway",
        "Ty for posting\nI took the Dominator from Shivis - Reiter Gateway",
        "Thanks to whoever posted Shivis - Reiter Gateway, got the Dom!",
    ],
)
def test_the_posters_own_verbs_on_a_gear_line_make_a_reply(text: str) -> None:
    assert parse_post(text).reply


@pytest.mark.parametrize(
    "text",
    [
        "G3 Dominator NV - grabbed\nG3 Artemis\nShivis - Reiter Gateway",
        "G3 Dominator NV (snagged by someone)\nG3 Artemis\nShivis - Reiter Gateway",
        "G3 Dom NV - purchased already\nG3 Artemis\nShivis - Reiter Gateway",
    ],
)
def test_a_claim_word_after_the_gear_is_its_status(text: str) -> None:
    post = parse_post(text)
    assert not post.reply
    assert [i.taken for i in post.items] == [True, False]


@pytest.mark.parametrize(
    ("item", "roles"),
    [
        (GearItem("Dominator", "suit", 3, ("Damage Resistance", "Night Vision")), ("combat",)),
        (GearItem("Maverick", "suit", 3, ("Reduced Tool Battery Consumption",)), ("salvage", "exobiology")),
        (GearItem("Artemis", "suit", 2, ("Extra Ammo Capacity",)), ("exobiology",)),  # dead on an Artemis
        (
            GearItem("Manticore Tormentor", "weapon", 3, ("Audio Masking", "Noise Suppressor")),
            ("combat", "stealth"),
        ),
        (GearItem("P12", "", 3), ()),
    ],
)
def test_who_an_item_suits(item: GearItem, roles: tuple[str, ...]) -> None:
    assert suits(item) == roles


def test_the_last_three_finds_stay_to_compare(plugin: tuple[ScoutPlugin, ScriptedTransport]) -> None:
    p, _t = plugin

    def decode(text: str) -> None:
        asked = p.gear.handled + 1
        p.api_post("/api/gear", {"text": text})
        _pump_until(
            p, lambda: p.gear.handled == asked and p.gear.status == "done" and p.gear.jumps_status == "done"
        )

    a, b, c = (
        POSTS["p2264-5"],
        "Keian Gu - Hahn Terminal\nG3 Dominator - Night Vision",
        "Keian Gu\nG2 Artemis",
    )
    decode(a)
    decode(b)
    assert [len(f.places) for f in p.gear.earlier] == [1]
    assert p.gear.earlier[0].places[0].items[0].name == "Maverick" and p.gear.earlier[0].decoded
    decode(b)  # the same post again: its find is replaced, nothing piles up
    assert len(p.gear.earlier) == 1
    decode(c)
    decode(a)
    shown = [p.gear.places[0].items[0].name] + [f.places[0].items[0].name for f in p.gear.earlier]
    assert shown == ["Maverick", "Artemis", "Dominator"]  # newest first, three in all
    state = json.loads(p.snapshot)["state"]["gear"]
    assert all(f["places"][0]["distance_ly"] is not None for f in state["earlier"])
    p.api_post("/api/gear", {"text": POSTS["p2267-3"]})  # a refused reply keeps what was found
    p.pump()
    assert p.gear.status == "error" and len(p.gear.earlier) == 2
    p.api_post("/api/gear", {"clear": True})
    p.pump()
    assert p.gear.earlier == () and p.gear.places == ()
