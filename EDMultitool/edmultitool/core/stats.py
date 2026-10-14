"""The game's own lifetime statistics: every Statistics entry (one per login) in time order, and what the
Stats tab shows from them: the newest totals by activity, a few figures worked out from them, how the headline
totals grew login by login, and what changed in the last week. Pure.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from edmultitool.core.deaths import Deaths, UiDeaths, ui_deaths
from edmultitool.core.events import StatisticsKnown, StatValue, stat_group_title

WEEK = timedelta(days=7)
FULL_FOR = timedelta(days=30)  # logins this much older than the newest keep only the headline totals
POINTS = 300  # at most this many points per sparkline (spread evenly beyond)
# the cards in the order shown; a group the game adds later comes after them
CARD_ORDER = (
    "overview", "explore", "exobio", "trade", "mining", "smuggling", "combat", "passengers", "rescue",
    "crime", "spent", "crafting", "materials", "crew", "multicrew", "squadron", "cqc", "thargoid", "carrier",
    "bank",
)  # fmt: skip
CARD_TITLES = {"overview": "Overview", "spent": "Where the credits went"}
HEADLINES = (
    "wealth", "systems_visited", "jumps", "distance", "explore_profit", "exobio_profit", "time_played",
)  # fmt: skip
PROFITS = (
    ("explore_profit", "Exploration data"), ("exobio_profit", "Exobiology"), ("trade_profit", "Trading"),
    ("mining_profit", "Mining"), ("smuggling_profit", "Black markets"), ("bounty_profit", "Bounties"),
    ("bond_profit", "Combat bonds"), ("foot_bond_profit", "On-foot combat bonds"),
    ("assassination_profit", "Assassinations"), ("rescue_profit", "Search and rescue"),
    ("multicrew_profit", "Multicrew"),
)  # fmt: skip
SPENDING = (
    ("spent_outfitting", "Outfitting"),
    ("spent_ships", "Ships"),
    ("spent_insurance", "Insurance (rebuys)"),
    ("spent_repairs", "Repairs"),
    ("spent_fuel", "Fuel"),
    ("spent_ammo", "Ammunition and limpets"),
    ("spent_weapons", "Personal weapons"),
    ("spent_suits", "Suits"),
    ("spent_suit_consumables", "Suit consumables"),
    ("spent_premium", "Premium ammunition"),
    ("spent_shuttles", "Shuttles"),
    ("crew_wages", "Crew wages"),
)


@dataclass(frozen=True, slots=True)
class UiStatRow:
    key: str
    label: str
    value: float
    unit: str  # "cr" | "ly" | "m" | "s" | "" (a count) | "cr/h"
    week: float | None = (
        None  # the change since the newest login at least a week older (None = none that old)
    )
    derived: bool = False  # worked out from the game's totals, not one of them


@dataclass(frozen=True, slots=True)
class UiStatCard:
    key: str
    title: str
    rows: tuple[UiStatRow, ...]
    zeros: int = 0  # figures still at 0, left out


@dataclass(frozen=True, slots=True)
class UiStatPoint:
    when: str  # ISO: the login
    value: float


@dataclass(frozen=True, slots=True)
class UiStatHeadline:
    key: str
    label: str
    unit: str
    value: float
    week: float | None
    points: tuple[UiStatPoint, ...]  # oldest first, one per login


@dataclass(frozen=True, slots=True)
class UiStatBar:
    label: str
    value: float  # credits


@dataclass(frozen=True, slots=True)
class UiStats:
    known: bool = False  # any Statistics entry read
    reading: bool = False  # the journals are still being read
    count: int = 0  # logins with statistics
    first: str = ""  # ISO: the oldest
    at: str = ""  # ISO: the newest (the totals shown)
    week_from: str = ""  # ISO: the login the week's changes are counted from ("" = none a week older)
    headlines: tuple[UiStatHeadline, ...] = ()
    profits: tuple[UiStatBar, ...] = ()  # by activity, most first, zeros left out
    spending: tuple[UiStatBar, ...] = ()  # by kind, most first, zeros left out
    cards: tuple[UiStatCard, ...] = ()
    empty: tuple[str, ...] = ()  # titles of the groups with nothing in them yet
    deaths: UiDeaths = field(default_factory=UiDeaths)  # read one by one (the game's totals count none)


class Stats:
    """Every login's statistics, oldest first, one per time. Logins more than `FULL_FOR` older than the newest
    keep only the headline totals (enough for the charts): years of logins stay small."""

    def __init__(self) -> None:
        self.meta: dict[str, StatValue] = {}  # key -> its card, label and unit (value: the newest login's)
        self.logins: list[tuple[str, dict[str, float]]] = []  # (ISO time, key -> total)
        self.deaths = Deaths()

    @property
    def last(self) -> str:
        return self.logins[-1][0] if self.logins else ""

    def add(self, event: StatisticsKnown) -> None:
        self._take(event.when, {v.key: v.value for v in event.values}, {v.key: v for v in event.values})

    def merge(self, other: "Stats") -> None:
        """Logins and deaths another fold read (the live journal's, while the history pass ran)."""
        self.deaths.merge(other.deaths)
        for when, values in other.logins:
            self._take(when, values, other.meta if when == other.last else None)

    def _take(self, when: str, values: dict[str, float], meta: dict[str, StatValue] | None) -> None:
        if any(seen == when for seen, _ in self.logins):
            return  # the same login read twice (the history pass and the live journal)
        if meta is not None and (not self.logins or when > self.last):
            self.meta = meta  # the newest login names the cards
        self.logins.append((when, dict(values)))
        self.logins.sort(key=lambda login: login[0])
        newest = _time(self.last)
        if newest is None:
            return
        for i, (when, values) in enumerate(self.logins):
            t = _time(when)
            if t is not None and t < newest - FULL_FOR and len(values) > len(HEADLINES):
                self.logins[i] = (when, {k: v for k, v in values.items() if k in HEADLINES})


def _time(iso: str) -> datetime | None:
    """A journal time; one without a zone is taken as UTC (the game's own always says so)."""
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo is not None else t.replace(tzinfo=UTC)


def _week_ago(logins: list[tuple[str, dict[str, float]]]) -> tuple[str, dict[str, float]] | None:
    """The newest login at least a week before the newest one."""
    newest = _time(logins[-1][0])
    if newest is None:
        return None
    for login in reversed(logins[:-1]):
        t = _time(login[0])
        if t is not None and t <= newest - WEEK:
            return login
    return None


def _thin(points: list[UiStatPoint]) -> tuple[UiStatPoint, ...]:
    if len(points) <= POINTS:
        return tuple(points)
    step = (len(points) - 1) / (POINTS - 1)
    return tuple(points[round(i * step)] for i in range(POINTS))


def _card_title(card: str) -> str:
    title = CARD_TITLES.get(card) or stat_group_title(card)
    if title:
        return title
    words = card.replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def ui_stats(stats: Stats) -> UiStats:
    deaths = ui_deaths(stats.deaths)
    if not stats.logins:
        return UiStats(deaths=deaths)
    at, now = stats.logins[-1]
    old = _week_ago(stats.logins)
    before = old[1] if old is not None else {}

    def week(key: str) -> float | None:
        return round(now[key] - before[key], 4) if key in before and key in now else None

    hours = now.get("time_played", 0.0) / 3600
    earned = sum(now.get(key, 0.0) for key, _ in PROFITS)
    derived: dict[str, list[UiStatRow]] = {"overview": [], "explore": [], "exobio": []}
    if earned:
        derived["overview"].append(
            UiStatRow("earned", "Earned (missions not included)", earned, "cr", derived=True)
        )
        if hours >= 1:
            per_hour = UiStatRow(
                "per_hour", "Per hour played (missions not included)", earned / hours, "cr/h", derived=True
            )
            derived["overview"].append(per_hour)
    if now.get("jumps"):
        avg = now.get("distance", 0.0) / now["jumps"]
        derived["explore"].append(UiStatRow("avg_jump", "Average jump", avg, "ly", derived=True))
    if now.get("organic_data") and now.get("exobio_profit"):
        avg = now["exobio_profit"] / now["organic_data"]
        derived["exobio"].append(UiStatRow("avg_sample", "Average per sample sold", avg, "cr", derived=True))

    by_card: dict[str, list[StatValue]] = {}
    for v in stats.meta.values():
        by_card.setdefault(v.group, []).append(v)
    order = [c for c in CARD_ORDER if c in by_card or derived.get(c)]
    order += sorted(c for c in by_card if c not in CARD_ORDER)
    cards, empty = [], []
    for card in order:
        values = by_card.get(card, [])
        rows = [UiStatRow(v.key, v.label, now[v.key], v.unit, week(v.key)) for v in values if now.get(v.key)]
        rows += derived.get(card, [])
        zeros = sum(1 for v in values if not now.get(v.key))
        if rows:
            cards.append(UiStatCard(card, _card_title(card), tuple(rows), zeros))
        else:
            empty.append(_card_title(card))

    headlines = []
    for key in HEADLINES:
        head = stats.meta.get(key)
        if head is None or key not in now:
            continue
        points = [UiStatPoint(when, values[key]) for when, values in stats.logins if key in values]
        headlines.append(UiStatHeadline(key, head.label, head.unit, now[key], week(key), _thin(points)))

    def bars(spec: tuple[tuple[str, str], ...]) -> tuple[UiStatBar, ...]:
        found = [UiStatBar(label, now[key]) for key, label in spec if now.get(key)]
        return tuple(sorted(found, key=lambda b: -b.value))

    return UiStats(
        known=True,
        count=len(stats.logins),
        first=stats.logins[0][0],
        at=at,
        week_from=old[0] if old is not None else "",
        headlines=tuple(headlines),
        profits=bars(PROFITS),
        spending=bars(SPENDING),
        cards=tuple(cards),
        empty=tuple(empty),
        deaths=deaths,
    )
