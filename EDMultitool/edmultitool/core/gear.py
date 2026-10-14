"""Decode a post from the forum's pre-upgraded gear-sharing thread: which suits and weapons are offered,
and where (system, station / settlement). Pure text work — Spansh is asked elsewhere which of the place
pieces are real systems and stations (`edmultitool.gear_lookup`); `locate` then ties every item to its place.

Players write these posts in many shapes (all seen in tests/data/gear_posts.txt):
    Shivis - Reiter Gateway            G2 Mav - Jump assist            Hahn Terminal - Keian Gu
    G3 | Karma C-44 | 1 mod Fast Handling | 5.625m CR | Ronhubbard Horizons
    G3 Maverick + Quieter Footsteps, 11,250,000 Credits @ LHS 5333 System, Satcher City (Large Pad)
so nothing here relies on order or punctuation: a line naming a suit or weapon is gear (whatever else it
holds is a place piece), a line of modification names belongs to the gear above it, and any other short line
is a place. A post that names its gear before its place ties the gear to the place after it.
"""

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, replace

from edmultitool.catalog.gear import (
    GRADE_POINTS,
    ITEMS,
    MOD_ROLES,
    MODS,
    QUALITY_POINTS,
    RARE_BONUS,
    RARE_MODS,
    ROLES,
    SLOTS_BY_GRADE,
    SUIT_BONUS,
    SUIT_COLUMN,
    SUIT_MOD_QUALITY,
    SUIT_ROLE,
    TIER_BONUS,
    WEAPON_GROUP,
    WEAPON_MOD_QUALITY,
    WEAPON_TIER,
)

MAX_TEXT = 5000  # characters of a pasted post we read (one post; the page's box allows as many)


@dataclass(frozen=True, slots=True)
class GearItem:
    name: str  # canonical ("Karma P-15"), or the post's own word when the item is not known ("P12")
    kind: str  # "suit" | "weapon" | "" (not known)
    grade: int | None
    mods: tuple[str, ...] = ()  # canonical modification names
    empty: bool = False  # the post says it has no modifications
    count: int = 1
    price: int | None = None  # Cr, as the post states it
    taken: bool = False  # the post says it is gone already
    line: int = 0


@dataclass(frozen=True, slots=True)
class PlaceText:
    pieces: tuple[str, ...]  # candidate system / station names, cleaned
    text: str  # the line it came from, cleaned
    inline: bool  # found on a gear line ("… | Ronhubbard Horizons")
    line: int = 0


Segment = GearItem | PlaceText


@dataclass(frozen=True, slots=True)
class ParsedPost:
    segments: tuple[Segment, ...]
    reply: bool  # opens like a reply ("Got the …", "Thanks", "Taken"), not an offer
    all_gone: bool  # "These are all gone"

    @property
    def items(self) -> tuple[GearItem, ...]:
        return tuple(s for s in self.segments if isinstance(s, GearItem))

    @property
    def pieces(self) -> tuple[str, ...]:
        """Every place piece once, in order of appearance."""
        seen: dict[str, None] = {}
        for s in self.segments:
            if isinstance(s, PlaceText):
                for p in s.pieces:
                    seen.setdefault(p, None)
        return tuple(seen)


# --- patterns ----------------------------------------------------------------------------------------
def _alt(patterns: tuple[str, ...]) -> str:
    return "(?:" + "|".join(sorted(patterns, key=len, reverse=True)) + ")"


_B, _E = r"(?<![a-z0-9])", r"(?![a-z0-9])"
_ITEM_RES = {name: re.compile(_B + _alt(spell) + _E) for name, (_k, _c, spell) in ITEMS.items()}
_MOD_RES = {name: re.compile(_B + _alt(spell) + _E) for name, (_k, spell) in MODS.items()}
_GRADE = re.compile(r"(?<![a-z0-9])(?:g|grade)\s?([1-5])(?![0-9])")
_GRADE_BEFORE = re.compile(r"(?:\d\s?x\s?)?(?:(?:g|grade)\s?[1-5]\s*[-|:]?\s*)?(?:\d\s?x\s?)?$")
_LEADING_GRADE = re.compile(r"^(?:\d\s?x\s?)?(?:g|grade)\s?([1-5])\b\s*[-|:]?\s*([a-z][\w-]*)")
_COUNT = re.compile(r"(?<![\w.,])([2-9])\s?x(?![a-z])|(?<![\w.,])([2-9])\s+(?=[a-z])")
_EMPTY = re.compile(r"\b(?:no|0|zero|mo) mods?\b|\bempty\b|\bunmodded\b|\bblank\b")
_TAKEN = re.compile(r"\b(?:taken|claimed|gone|snagged|grabbed|purchased|no longer)\b")
_PRICE = re.compile(
    r"(?<![\w.,])(\d{1,3}(?:[ ,]\d{3})+|\d+(?:\.\d+)?)\s*([mk])?\s*(?:cr|credits)\b"
    r"|(?:^|[-|]\s*)(\d{1,3}(?:,\d{3})+)\s*$"
)
_DASHES = "-" + chr(0x2013) + chr(0x2014)  # hyphen, en dash, em dash
_ZW = chr(0x200B)  # zero-width space (forum posts carry them)
_TRIM = " .!?:;'\"" + _DASHES  # around a name piece
_PAD_NOTE = re.compile(r"\([^()]*\b(?:pads?|platforms?|outpost|planetary|port|landing)\b[^()]*\)", re.I)
_SEP = re.compile(
    rf"\s+[{_DASHES}]+\s*|\s*[{_DASHES}]+\s+|\s*[|,:;@/]\s*|\.\s+|\s+(?:at|in)\s+|\s+w/\s*|\s+\+\s*", re.I
)
_PROC = re.compile(
    r"(?i)\b([a-z][a-z0-9'.]*(?: [a-z0-9'.]+){0,5}? [a-z]{2}-[a-z] [a-h]\d+(?:-\d+)?)(?![\w-])"
)
_CATALOGUE = re.compile(
    r"(?i)^((?:hip|hd|hr|lhs|ltt|lft|lp|bd|cd|gcrv|gliese|gl|ross|wolf|ngc|stf|cpd|tyc|bpm)"
    r"\s?[+-]?\d+(?:[- ]\d+)?)\s+(\D.*)$"
)
_CATALOGUE_GLUED = re.compile(r"(?i)^(hip|hd|lhs|ltt|lft|hr|gcrv)(\d)")
_SENTENCE = re.compile(rf"(?<=[!?])[\s{_ZW}]*(?=\S)|(?<=\.\.\.)\s*(?=[A-Z])")
# a reply claims gear for itself: purchased / grabbed / taken / got the …
_CLAIM = re.compile(
    r"\b(?:purchased|bought|grabbed|snagged|snag|taken|taking|took|i'll take|was able"
    r"|got (?:the|this|it|both|them|one|my|that))\b"
)
_OWN_CLAIM = re.compile(
    r"\b(?:purchased|bought|grabbed|snagged|took|i'll take|got (?:the|this|it|both|them|one|my|that))\b"
)
_NOT_A_NAME = re.compile(r"\b(?:stock|restock(?:ed)?|week|today|tomorrow|tonight)\b")
_REPLY_START = re.compile(
    r"\b(?:thanks?|thank you|thx|ty|got (?:the|this|it|both|them|one|my|that)|grabbed|purchased|snagged|snag"
    r"|taken|taking|took|bought|i'll take|was able)\b"
)
_CHATTER = re.compile(
    r"\b(?:thanks?|thank you|thx|ty|o7|got|grabbed|purchased|snagged|snag|taken|taking|took|bought|gone"
    r"|available|reply|looking|message|cross|writing|nothing|everything|write|confirmed|still|lol|please"
    r"|already|unfortunately|claimed|finishes)\b|!"
)


def _words(text: str) -> frozenset[str]:
    return frozenset(text.split())


_STOP = _words(
    "a an the and or but also i i'm i'll it its it's is are was were be this that these those there here "
    "my me you your we our they them he she her his of for to from on with without by as if so not no "
    "pick up same one all both any some more just only other has have had do does did will can"
)
_FILLER = _words(
    "suit suits with w and plus mod mods slot slots cr credits each x both the a an no empty blank taken "
    "claimed gone sold snagged now also left pad pads large medium small l m s weapon weapons pistol "
    "rifle"
)
_STATION_WORDS = _words(
    "terminal port hub dock station city ring orbital relay landing base installation outpost depot works "
    "holdings mines refinery town point enterprise enterprises horizons legacy vision assembly settlement "
    "lab laboratory industries market folly refuge survey keep stop sanctuary progress barracks reach "
    "gateway colony camp prospect garrison complex site hold armoury exchange facility plant yard hangar "
    "construction constructions estate platform tower outlook residence beacon"
)


def name_key(text: str) -> str:
    """Name comparison key: no accents, no letter case, letters and digits only."""
    text = unicodedata.normalize("NFKD", text)
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in text if not unicodedata.combining(c)).casefold())


def station_like(piece: str) -> bool:
    """Ends in a word stations and settlements end in ("Reiter Gateway", "North town")."""
    words = piece.casefold().split()
    return len(words) >= 2 and words[-1].strip(".") in _STATION_WORDS


# --- parsing -----------------------------------------------------------------------------------------
def _clean_line(line: str) -> str:
    line = line.replace(_ZW, " ").replace("\t", " ")
    line = _PAD_NOTE.sub(" ", line)
    line = re.sub(r"\(\d\)", " ", line)  # "(1)/Improved Batt. Cap." — the number of mods
    line = re.sub(r"[()<>\[\]{}*]", " ", line)
    line = re.sub(r"(?i)\b(g[1-5])(?=[a-z])", r"\1 ", line)  # "G3Maverick"
    return re.sub(r"\s+", " ", line).strip()


def _place_pieces(text: str, *, inline: bool = False) -> tuple[str, ...]:
    """Candidate names in a place line, or in what a gear line holds besides its gear (`inline`: there a
    piece ends where plain words start, "Condovichs has a" → "Condovichs")."""
    text = re.sub(r"(?i)^\s*at\s+", "", text)
    text = re.sub(r"(?i)\s+system\b", " ", text)
    out: list[str] = []
    for raw in _SEP.split(text):
        piece = re.sub(r"\s+", " ", raw).strip(_TRIM)
        if not piece:
            continue
        piece = _CATALOGUE_GLUED.sub(r"\1 \2", piece)
        if inline:
            words = piece.split()
            cut = next((i for i, w in enumerate(words[1:], 1) if w.casefold() in _STOP), len(words))
            piece = " ".join(words[:cut])
        parts = [piece]
        m = _PROC.search(piece)
        if m:  # "Col 285 Sector MG-O b22-2 U.T.S.P DR. Helena Russel" → the system and the rest
            parts = [piece[: m.start()], m.group(1), piece[m.end() :]]
        else:
            c = _CATALOGUE.match(piece)
            if c:  # "HIP 68154 Becat Refuge"
                parts = [c.group(1), c.group(2)]
        for p in parts:
            p = p.strip(_TRIM)
            if inline and " " not in p and p.islower():
                continue  # "time", "prior": a plain word, not a name
            if _is_piece(p):
                out.append(p)
    return tuple(dict.fromkeys(out))


def _is_piece(p: str) -> bool:
    words = p.split()
    if not words or len(words) > 6 or sum(c.isalpha() for c in p) < 2:
        return False
    low = [w.casefold().strip(".,'") for w in words]
    if low[0] in _STOP or all(w in _STOP or w in _FILLER for w in low) or _CHATTER.search(p.casefold()):
        return False
    if _NOT_A_NAME.search(p.casefold()):  # "Stock for this week"
        return False
    if _PRICE.search(p.casefold()) or re.search(
        r"(?i)\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b \d", p
    ):
        return False
    return not any(r.search(p.casefold()) for r in _ITEM_RES.values())


def _parse_price(m: re.Match[str]) -> int | None:
    if m.group(3):
        return int(m.group(3).replace(",", ""))
    number, unit = m.group(1).replace(" ", "").replace(",", ""), m.group(2)
    try:
        value = float(number)
    except ValueError:
        return None
    value *= {"m": 1e6, "k": 1e3}.get(unit or "", 1)
    return round(value) if value >= 1000 else None


def _find_mods(text: str, kind: str) -> tuple[list[str], list[tuple[int, int]]]:
    hits: list[tuple[int, int, str]] = []
    for name, rx in _MOD_RES.items():
        fits = MODS[name][0]
        if kind and fits != kind:
            continue
        hits.extend((m.start(), m.end(), name) for m in rx.finditer(text) if m.end() > m.start())
    hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))
    mods: list[str] = []
    spans: list[tuple[int, int]] = []
    end = -1
    for start, stop, name in hits:
        if start < end:
            continue  # overlaps a longer, earlier spelling
        end = stop
        spans.append((start, stop))
        if name not in mods:
            mods.append(name)
    return mods, spans


def _item_spans(low: str) -> list[tuple[int, int, str]]:
    hits: list[tuple[int, int, str]] = []
    for name, rx in _ITEM_RES.items():
        hits.extend((m.start(), m.end(), name) for m in rx.finditer(low))
    hits.sort()
    merged: list[tuple[int, int, str]] = []
    for start, stop, name in hits:
        if merged:
            ps, pe, pn = merged[-1]
            if start < pe or (pn == name and len(low[pe:start].split()) <= 2):
                merged[-1] = (ps, max(pe, stop), pn)  # "TK apelion laser rifle" is one item
                continue
        merged.append((start, stop, name))
    return merged


def _blank(text: str, spans: list[tuple[int, int]]) -> str:
    chars = list(text)
    for a, b in spans:
        for i in range(a, b):
            chars[i] = " "
    return "".join(chars)


def _gear_chunk(chunk: str, low: str, name: str, line_no: int, taken_line: bool) -> tuple[GearItem, str]:
    """One item from its part of a gear line; returns it and what is left of the text (place pieces)."""
    kind = ITEMS[name][0] if name in ITEMS else ""
    spans: list[tuple[int, int]] = []
    item = _ITEM_RES[name].search(low) if name in ITEMS else None
    if item:
        spans.append(item.span())
    grade_m = _GRADE.search(low)
    grade = int(grade_m.group(1)) if grade_m else None
    spans.extend(m.span() for m in _GRADE.finditer(low))
    mods, mod_spans = _find_mods(low, kind)
    spans += mod_spans + _find_mods(low, "")[1]  # a mod that does not fit is no place name either
    count = 1
    for m in _COUNT.finditer(low):
        if item is None or m.start() < item.start():
            count = int(m.group(1) or m.group(2))
            spans.append(m.span())
            break
    if (
        re.search(r"\bboth\b", low)
        and count == 1
        and re.search(r"\b2\b|\bboth\b", low[: item.start() if item else 0])
    ):
        count = 2
    price = None
    for m in _PRICE.finditer(low):
        price = _parse_price(m)
        spans.append(m.span())
    empty = bool(_EMPTY.search(low)) and not mods
    spans.extend(m.span() for m in _EMPTY.finditer(low))
    spans.extend(m.span() for m in re.finditer(r"\b\d\s+mods?\b|\bdead mod\b|\bmods?\b", low))
    taken = taken_line or bool(_TAKEN.search(low))
    spans.extend(m.span() for m in _TAKEN.finditer(low))
    rest = _blank(chunk, spans)
    return GearItem(name, kind, grade, tuple(mods), empty, count, price, taken, line_no), rest


def _sentences(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text[:MAX_TEXT].splitlines():
        lines.extend(s for s in _SENTENCE.split(raw) if s.strip())
    return lines


def _is_reply(sentences: list[str]) -> bool:
    """A reply, not an offer: an opening line without gear that claims some ("Purchased", "Grabbed",
    "Taken both:"), or a thank-you that names no place where gear is still on offer ("Got the tourmentor,
    thanks"). A polite opening over a list of places is an offer; so is a first line of gone gear with a
    place further down."""
    lines = [_clean_line(r) for r in sentences]
    lines = [line for line in lines if line]
    if not lines:
        return False
    first = lines[0].casefold()
    # the claim opens the post, or follows a thank-you ("Many thanks!" / "I purchased the below:")
    opening = lines[:2] if _REPLY_START.search(first) else lines[:1]
    for line in opening:
        low = line.casefold()
        spans = _item_spans(low)
        claim = (_OWN_CLAIM if spans else _CLAIM).search(low)
        # on a gear line only the poster's own verb BEFORE the gear claims it ("got the Dom");
        # after it ("Dom NV - grabbed", "taken") it is the item's status
        if claim and (not spans or claim.start() < spans[0][0]):
            return True
    if not _REPLY_START.search(first):
        return False
    spans = _item_spans(first)
    if spans:
        segs = _gear_line(lines[0], first, spans, 0)
        items = [s for s in segs if isinstance(s, GearItem)]
        if not all(i.taken for i in items) and any(isinstance(s, PlaceText) for s in segs):
            return False  # "Thank you all. Shivis - Reiter Gateway has G3 Dom NV"
    return not any(_has_place(line) for line in lines[1:])


def _has_place(line: str) -> bool:
    low = line.casefold()
    if _CHATTER.search(low) and not _item_spans(low):
        return False
    spans = _item_spans(low)
    if spans:
        return any(isinstance(s, PlaceText) for s in _gear_line(line, low, spans, 0))
    return bool(_place_pieces(line))


def parse_post(text: str) -> ParsedPost:
    segments: list[Segment] = []
    sentences = _sentences(text)
    reply = _is_reply(sentences)
    all_gone = False
    for line_no, raw in enumerate(sentences):
        line = _clean_line(raw)
        if not line:
            continue
        low = line.casefold()
        spans = _item_spans(low)
        if re.search(r"\b(?:these|they) are all gone\b|\ball gone\b", low):
            all_gone = True
        if spans:
            segments.extend(_gear_line(line, low, spans, line_no))
            continue
        lead = _LEADING_GRADE.match(low)
        if lead and not _MOD_RES["Scope"].fullmatch(
            lead.group(2)
        ):  # "G3 P12 - Scoped": an item we do not know
            word = line[lead.start(2) : lead.end(2)]
            item, _rest = _gear_chunk(line, low, "", line_no, False)
            segments.append(replace(item, name=word, grade=int(lead.group(1))))
            continue
        mods, mod_spans = _find_mods(low, "")
        price_m = _PRICE.search(low)
        leftover = _blank(low, mod_spans + ([price_m.span()] if price_m else []))
        leftover_words = [
            w for w in re.findall(r"[a-z0-9']+", leftover) if w not in _FILLER and w not in _STOP
        ]
        last = next((i for i in range(len(segments) - 1, -1, -1) if isinstance(segments[i], GearItem)), None)
        if (mods or price_m) and not leftover_words and last is not None:
            prev = segments[last]
            assert isinstance(prev, GearItem)
            item = prev
            fits = [m for m in mods if (not item.kind or MODS[m][0] == item.kind) and m not in item.mods]
            price = _parse_price(price_m) if price_m else item.price
            segments[last] = replace(
                item, mods=item.mods + tuple(fits), price=price, empty=item.empty and not fits
            )
            continue
        if _EMPTY.fullmatch(low.strip(" .")) or re.fullmatch(r"\d+ blanks?", low):
            continue
        if _CHATTER.search(low) or len(line.split()) > 12:
            continue
        pieces = _place_pieces(line)
        if pieces:
            segments.append(PlaceText(pieces, line, False, line_no))
    return ParsedPost(tuple(segments), reply, all_gone)


def _gear_line(line: str, low: str, spans: list[tuple[int, int, str]], line_no: int) -> list[Segment]:
    taken_line = bool(_TAKEN.search(low))
    starts = []
    for start, _stop, _name in spans:
        before = _GRADE_BEFORE.search(low[:start])
        starts.append(before.start() if before else start)
    starts[0] = 0
    items: list[GearItem] = []
    rest_parts: list[str] = []
    for i, (_s, _e, name) in enumerate(spans):
        a, b = starts[i], starts[i + 1] if i + 1 < len(spans) else len(line)
        item, rest = _gear_chunk(line[a:b], low[a:b], name, line_no, taken_line)
        items.append(item)
        rest_parts.append(rest)
    out: list[Segment] = []
    pieces = _place_pieces(" | ".join(rest_parts), inline=True)
    if pieces:
        out.append(PlaceText(pieces, line, True, line_no))
    out.extend(items)
    return out


# --- places ------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class StationInfo:
    name: str
    type: str  # Spansh's station type, e.g. "Ocellus Starport", "Outpost", "Odyssey Settlement"
    pad: str  # largest landing pad: "L" / "M" / "S" / "" (not known)
    arrival_ls: float | None
    planetary: bool
    system_id64: int
    system_name: str
    market: bool | None = None  # it has a commodity market (None = not known)


@dataclass(frozen=True, slots=True)
class SystemInfo:
    id64: int
    name: str
    x: float
    y: float
    z: float
    stations: tuple[StationInfo, ...] = ()


@dataclass(frozen=True, slots=True)
class Lookup:
    """What Spansh said about a post's place pieces (keys: `name_key(piece)`)."""

    systems: Mapping[str, SystemInfo]  # pieces that are system names
    stations: Mapping[str, tuple[StationInfo, ...]]  # station-name search hits, any system
    station_systems: Mapping[int, SystemInfo]  # the systems of those hits, by id64
    failed: frozenset[str] = frozenset()  # pieces Spansh failed on (name_key): unknown, not absent


@dataclass(frozen=True, slots=True)
class GearPlace:
    system: SystemInfo | None  # None = not found
    station: StationInfo | None
    items: tuple[GearItem, ...]
    text: str  # what the post said
    note: str = ""  # why it was not found, or a doubt


def similarity(a: str, b: str) -> float:
    """1 - edits / longer length (Levenshtein): "brookesterminal" ~ "brooksterminal" = 0.93. Our own:
    EDMC's bundled Python has no `difflib`."""
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, cb in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
    return 1.0 - row[-1] / max(len(a), len(b))


def match_station(piece: str, stations: tuple[StationInfo, ...]) -> StationInfo | None:
    """The station a piece names among `stations`: same name ignoring case, accents, spaces and
    punctuation, else the one clearly closest in spelling ("Brookes terminal" → "Brooks Terminal")."""
    key = name_key(piece)
    for s in stations:
        if name_key(s.name) == key:
            return s
    if len(key) < 6:
        return None
    scored = sorted(
        ((similarity(key, name_key(s.name)), s) for s in stations),
        key=lambda t: -t[0],
    )
    if scored and scored[0][0] >= 0.85 and (len(scored) == 1 or scored[1][0] < scored[0][0] - 0.05):
        return scored[0][1]
    return None


def _resolve_line(
    seg: PlaceText, lookup: Lookup, current: SystemInfo | None, found: list[SystemInfo]
) -> tuple[SystemInfo | None, StationInfo | None, str]:
    systems = [lookup.systems[name_key(p)] for p in seg.pieces if name_key(p) in lookup.systems]
    others = [p for p in seg.pieces if name_key(p) not in lookup.systems]
    prefer = [s for s in ([*systems[:1], current, *found]) if s is not None]
    note = ""
    for piece in others:
        for sys in dict.fromkeys(prefer):
            st = match_station(piece, sys.stations)
            if st is not None:
                return sys, st, ""
        hits = lookup.stations.get(name_key(piece), ())
        if len(hits) == 1:
            home = lookup.station_systems.get(hits[0].system_id64)
            if home is not None:
                return home, hits[0], ""
        elif len(hits) > 1 and not systems:
            # "Bartlett City, Scorpio Sector JH-V B2-5": the system's name has a typo, the station's
            # name is shared — the station whose system is spelled most like another piece of the line
            spelled = [name_key(p) for p in others if p != piece]
            best = max(
                ((similarity(k, name_key(h.system_name)), h) for h in hits for k in spelled),
                key=lambda t: t[0],
                default=None,
            )
            home = lookup.station_systems.get(best[1].system_id64) if best and best[0] >= 0.85 else None
            if best is not None and home is not None:
                return home, best[1], f"taken as {home.name}"
            note = f"{len(hits)} stations are called {hits[0].name}; add the system"
    if systems:
        missing = [p for p in others if station_like(p)]
        return systems[0], None, f"{missing[0]} is not listed at Spansh" if missing else ""
    return None, None, note


def locate(post: ParsedPost, lookup: Lookup) -> tuple[GearPlace, ...]:
    """Tie every item to its place. Gear before any place (the post's style) goes with the place after it;
    otherwise gear goes with the place above it. A gear line's own place piece wins over both."""
    gear_first = bool(post.segments) and isinstance(post.segments[0], GearItem)
    places: list[GearPlace] = []
    found: list[SystemInfo] = []
    pending: list[GearItem] = []
    current: GearPlace | None = None
    inline_line = -1
    after_place = False  # the segment before was a place line
    for seg in post.segments:
        if isinstance(seg, PlaceText):
            sys, st, note = _resolve_line(seg, lookup, current.system if current else None, found)
            if sys is None and current is not None and current.system is not None and seg.inline:
                # an unknown piece on a gear line ("… - CLAIMED") keeps the place above
                inline_line = seg.line
                continue
            if sys is not None and sys not in found:
                found.append(sys)
            if (
                after_place
                and current is not None
                and current.system == sys
                and current.station is None
                and st is not None
                and not seg.inline
            ):  # "Synuefe QQ-H b57-2" then "Morley Keep": one place
                places[-1] = current = replace(
                    current, station=st, text=f"{current.text} · {seg.text}", note=""
                )
                continue
            after_place = not seg.inline
            unasked = any(name_key(p) in lookup.failed for p in seg.pieces)
            missing = "Spansh did not answer for this name; try again" if unasked else "not found"
            current = GearPlace(sys, st, (), seg.text, note if sys else note or missing)
            places.append(current)
            inline_line = seg.line if seg.inline else -1
            if pending and not seg.inline:
                places[-1] = current = replace(current, items=tuple(pending))
                pending = []
            continue
        after_place = False
        if seg.line == inline_line and current is not None:
            places[-1] = current = replace(current, items=(*current.items, seg))
        elif gear_first or current is None:
            pending.append(seg)
        else:
            places[-1] = current = replace(current, items=(*current.items, seg))
    if pending:
        if places:
            places[-1] = replace(places[-1], items=(*places[-1].items, *pending))
        else:
            places.append(GearPlace(None, None, tuple(pending), "", "no place named"))
    return _merge(places)


def _merge(places: list[GearPlace]) -> tuple[GearPlace, ...]:
    """Places with the same system and station become one; places without gear are dropped when any
    place has gear (a system line followed by its station line)."""
    out: dict[tuple[object, ...], GearPlace] = {}
    for p in places:
        key: tuple[object, ...] = (
            (p.system.id64, p.station.name if p.station else None) if p.system else ("?", p.text)
        )
        if key in out:
            out[key] = replace(out[key], items=out[key].items + p.items)
        else:
            out[key] = p
    merged = list(out.values())
    if any(p.items for p in merged):
        merged = [p for p in merged if p.items]
    return tuple(merged)


# --- the separate fields, and the request -----------------------------------------------------------
@dataclass(frozen=True, slots=True)
class GearRequest:
    """What the page sent: a pasted post, or the separate fields (`fields`)."""

    text: str = ""
    system: str = ""
    station: str = ""
    gear: str = ""
    fields: bool = False


def parse_fields(system: str, station: str, gear: str) -> ParsedPost:
    """The separate fields: one place (system and/or station as typed) and the gear lines."""
    pieces = tuple(p for p in (" ".join(system.split()), " ".join(station.split())) if p)
    text = " - ".join(pieces)
    items = tuple(replace(i, line=1) for i in parse_post(gear).items)
    segments: tuple[Segment, ...] = ((PlaceText(pieces, text, False, 0),) if pieces else ()) + items
    return ParsedPost(segments, reply=False, all_gone=False)


def mod_quality(item: str, mod: str) -> str:
    """How players rate a modification on this item: "top" / "good" / "weak" / "dead"."""
    if item in SUIT_COLUMN and mod in SUIT_MOD_QUALITY:
        return SUIT_MOD_QUALITY[mod][SUIT_COLUMN[item]]
    if item in WEAPON_GROUP and mod in WEAPON_MOD_QUALITY:
        return WEAPON_MOD_QUALITY[mod][WEAPON_GROUP[item]]
    return "weak"


def suits(item: GearItem) -> tuple[str, ...]:
    """Who the item is for: a suit's own job (Dominator combat, Maverick salvage, Artemis exobiology), a
    weapon combat; plus what its good mods are for ("stealth" for the silencing mods). Dead and weak mods
    add nothing."""
    if item.name not in ITEMS:
        return ()
    roles = {SUIT_ROLE.get(item.name, "combat")}
    for mod in item.mods:
        if mod_quality(item.name, mod) in ("top", "good"):
            roles.update(MOD_ROLES.get(mod, ()))
    return tuple(r for r in ROLES if r in roles)


def rate(item: GearItem) -> tuple[int, str]:
    """1-5 stars and one line why; 0 when the item or its grade is
    not known. Grade, the item itself, and every slot the grade has: a top mod is worth most, a free slot
    beats a weak mod (you pick), a dead mod costs (a mod can never be removed). The price is left out: it
    follows grade and mod count only, never how good the mods are."""
    if item.name not in ITEMS or item.grade is None:
        return 0, "item not known" if item.name not in ITEMS else "grade not given"
    slots = SLOTS_BY_GRADE[item.grade]
    points = GRADE_POINTS[item.grade]
    if item.kind == "suit":
        points += SUIT_BONUS
        what = f"G{item.grade} {item.name}"
    else:
        tier = WEAPON_TIER[item.name]
        points += TIER_BONUS[tier]
        what = f"G{item.grade} {item.name} (tier {tier})"
    parts: list[str] = []
    for mod in item.mods[:slots]:
        q = mod_quality(item.name, mod)
        points += QUALITY_POINTS[q]
        if mod in RARE_MODS and q in ("top", "good"):
            points += RARE_BONUS
            q += ", rare"
        parts.append(f"{mod} {q}")
    free = max(0, slots - len(item.mods))
    points += free * QUALITY_POINTS["empty"]
    if free and not item.mods and not item.empty:
        parts.append(f"mods not stated: rated as {free} free slot{'s' if free > 1 else ''}")
    elif free:
        parts.append(f"{free} free slot{'s' if free > 1 else ''}")
    stars = 1 if points < 1.5 else 2 if points < 2.5 else 3 if points < 3.5 else 4 if points < 4.5 else 5
    return stars, what + (": " + "; ".join(parts) if parts else "")
