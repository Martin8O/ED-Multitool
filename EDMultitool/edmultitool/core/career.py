"""Your career, folded from the journal: standing with the superpowers, navy ranks, the Powerplay pledge
with its merits, and how each moved over time. Pure.

Unlike the other folds, `Career` is changed in place: a long history brings tens of thousands of merit
entries, and the history pass and the live journal fold into one object owned by one thread at a time (the
pass's, then the main thread's once handed over).
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from edmultitool.core.events import (
    RANKS,
    SUPERPOWERS,
    CareerEvent,
    MeritsEarned,
    PledgeChanged,
    PowerplayKnown,
    PowerRanked,
    ProgressMoved,
    RanksKnown,
    ReputationKnown,
)
from edmultitool.core.rank_guide import RANK_GUIDE

NAVIES = ("Federation", "Empire")
_ELITE = ("Elite", "Elite I", "Elite II", "Elite III", "Elite IV", "Elite V")
# every rank the game keeps -> its titles by number (Elite I to V where the game has them)
RANK_TITLES = {
    "Combat": (
        "Harmless", "Mostly Harmless", "Novice", "Competent", "Expert", "Master", "Dangerous",
        "Deadly", *_ELITE,
    ),
    "Trade": (
        "Penniless", "Mostly Penniless", "Peddler", "Dealer", "Merchant", "Broker", "Entrepreneur",
        "Tycoon", *_ELITE,
    ),
    "Explore": (
        "Aimless", "Mostly Aimless", "Scout", "Surveyor", "Trailblazer", "Pathfinder", "Ranger",
        "Pioneer", *_ELITE,
    ),
    "Soldier": (
        "Defenceless", "Mostly Defenceless", "Rookie", "Soldier", "Gunslinger", "Warrior", "Gladiator",
        "Deadeye", *_ELITE,
    ),
    "Exobiologist": (
        "Directionless", "Mostly Directionless", "Compiler", "Collector", "Cataloguer", "Taxonomist",
        "Ecologist", "Geneticist", *_ELITE,
    ),
    "CQC": (
        "Helpless", "Mostly Helpless", "Amateur", "Semi Professional", "Professional", "Champion",
        "Hero", "Legend", "Elite",
    ),
    "Federation": (
        "None", "Recruit", "Cadet", "Midshipman", "Petty Officer", "Chief Petty Officer", "Warrant Officer",
        "Ensign", "Lieutenant", "Lieutenant Commander", "Post Commander", "Post Captain", "Rear Admiral",
        "Vice Admiral", "Admiral",
    ),
    "Empire": (
        "None", "Outsider", "Serf", "Master", "Squire", "Knight", "Lord", "Baron", "Viscount", "Count",
        "Earl", "Marquis", "Duke", "Prince", "King",
    ),
}  # fmt: skip
# A superpower standing (-100..100) → the game's word for it: each tier from its lower bound up.
REPUTATION_TIERS = (
    (-100.0, "Hostile"),
    (-90.0, "Unfriendly"),
    (-35.0, "Neutral"),
    (4.0, "Cordial"),
    (35.0, "Friendly"),
    (90.0, "Allied"),
)
# Powerplay ranks by merits in all (published community table): rank 1, then these; past rank 5 every
# 8,000 merits more. The game's own rank wins: it stays 0 until the power's introductory missions are done.
PP_RANK_MERITS = (0, 2_000, 5_000, 9_000, 15_000)
PP_RANK_STEP = 8_000
CYCLE_START = (3, 7)  # a Powerplay week begins on Thursday (weekday 3) at 07:00 UTC
HISTORY_POINTS = 300  # at most this many points per chart (one per day, thinned evenly beyond)
WEEKS_SHOWN = 12


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _day(when: str) -> str:
    return when[:10]


def cycle_start(when: datetime) -> datetime:
    """The start of the Powerplay week `when` falls in."""
    weekday, hour = CYCLE_START
    t = when.astimezone(UTC)
    start = (t - timedelta(hours=hour)).replace(hour=hour, minute=0, second=0, microsecond=0)
    return start - timedelta(days=(start.weekday() - weekday) % 7)


def _parse(when: str) -> datetime | None:
    try:
        t = datetime.fromisoformat(when.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


@dataclass(slots=True)
class Career:
    standings: dict[str, float] = field(default_factory=dict)  # superpower → -100..100, the newest known
    ranks: dict[str, int] = field(default_factory=dict)  # every rank the game keeps → its number
    progress: dict[str, int] = field(default_factory=dict)  # → percent to the next
    # ranks moved on (a promotion, a sale) since the game last wrote their percent: known at the next login
    stale: set[str] = field(default_factory=set)
    power: str | None = None  # pledged to; "" = none, None = no Powerplay entry seen
    merits: int | None = None  # in all, for the power pledged to
    pp_rank: int | None = None  # the game's own rank with that power (None = no entry says it)
    # the newest PowerplayRank (power, rank, when): written just BEFORE the Join / Defect it belongs to
    last_ranked: tuple[str, int, str] | None = None
    pledged_since: str = ""  # ISO time of the pledge
    first: str = ""  # the oldest career entry seen
    last: str = ""  # the newest
    standing_days: dict[str, tuple[float | None, ...]] = field(default_factory=dict)  # day → SUPERPOWERS
    navy_days: dict[str, tuple[float | None, ...]] = field(default_factory=dict)  # day → NAVIES, rank + %/100
    # per Powerplay week (its start, ISO): the merits in all last known in it, the merits the entries say were
    # earned in it, and the weeks a pledge began in (merits in all start again from 0)
    week_totals: dict[str, int] = field(default_factory=dict)
    week_gains: dict[str, int] = field(default_factory=dict)
    pledge_weeks: set[str] = field(default_factory=set)

    def add(self, event: CareerEvent) -> None:
        when = event.when
        self.first = min(self.first, when) if self.first else when
        self.last = max(self.last, when)
        match event:
            case ReputationKnown():
                self.standings.update(event.standings)
                self.standing_days[_day(when)] = tuple(
                    None if (v := self.standings.get(n)) is None else round(v, 1) for n in SUPERPOWERS
                )
            case RanksKnown():
                if event.progress:
                    self.progress.update(event.values)
                    self.stale.difference_update(kind for kind, _ in event.values)
                else:
                    if len(event.values) == 1:  # a promotion: that rank's progress starts again
                        self.progress[event.values[0][0]] = 0
                        self.stale.add(event.values[0][0])  # what pushed it over may count already
                    self.ranks.update(event.values)
                self.navy_days[_day(when)] = tuple(self._navy_level(n) for n in NAVIES)
            case PowerplayKnown():
                self.power, self.merits = event.power, event.merits
                if event.rank is not None:
                    self.pp_rank = event.rank
                start = _parse(when)
                if start is not None:
                    since = start - timedelta(seconds=event.seconds_pledged)
                    self.pledged_since = _iso(since)
                    self.week_totals[_iso(cycle_start(start))] = event.merits
            case MeritsEarned():
                if self.power is None:
                    self.power = event.power
                # the game's own total wins: entries written in the same second can come out of order, and
                # the total then shows some of their merits were never counted
                self.merits = event.total if event.total is not None else (self.merits or 0) + event.gained
                t = _parse(when)
                if t is not None:
                    week = _iso(cycle_start(t))
                    self.week_gains[week] = self.week_gains.get(week, 0) + max(event.gained, 0)
                    self.week_totals[week] = self.merits
            case PledgeChanged():
                self.power = event.power
                self.merits = 0 if event.power else None
                ranked = self.last_ranked  # written in the same second just before, for the new power
                same = ranked is not None and ranked[0] == event.power and ranked[2] == when
                self.pp_rank = ranked[1] if same and ranked is not None else None
                t = _parse(when)
                self.pledged_since = _iso(t) if event.power and t is not None else ""
                if t is not None:
                    week = _iso(cycle_start(t))
                    self.pledge_weeks.add(week)
                    self.week_totals[week] = 0
                    self.week_gains[week] = 0
            case PowerRanked():
                self.last_ranked = (event.power, event.rank, when)
                if event.power == self.power or self.power is None:
                    self.pp_rank = event.rank
            case ProgressMoved():
                self.stale.add(event.rank)

    def week_merits(self) -> dict[str, int]:
        """Merits earned in each Powerplay week: the rise of the game's total over the week (from 0 in a week
        a pledge began in); the entries' own sum only where no earlier total is known."""
        out: dict[str, int] = {}
        before: int | None = None
        for week in sorted(self.week_totals.keys() | self.week_gains.keys()):
            end = self.week_totals.get(week)
            if end is None:
                earned = self.week_gains.get(week, 0)
            elif week in self.pledge_weeks:
                earned = end
            elif before is not None:
                earned = end - before
            else:
                earned = self.week_gains.get(week, 0)
            out[week] = max(earned, 0)
            before = end if end is not None else (before + earned if before is not None else None)
        return out

    def _navy_level(self, navy: str) -> float | None:
        rank = self.ranks.get(navy)
        if rank is None:
            return None
        return rank + min(self.progress.get(navy, 0), 100) / 100


# --- what the page shows ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UiStanding:
    name: str  # "Federation"
    value: float  # -100..100, 1 decimal place
    tier: str  # "Friendly"
    next_tier: str  # "Allied" ("" = the top already)
    to_next: float  # points still to go to `next_tier`
    tier_pct: int  # how far into the current tier, 0..100


@dataclass(frozen=True, slots=True)
class UiRankStep:
    title: str  # "Petty Officer"
    unlocks: str  # what reaching it opens ("" = nothing of note)


@dataclass(frozen=True, slots=True)
class UiRank:
    kind: str  # the game's name: "Combat", "Soldier", "Federation", …
    rank: int  # 0..13 (navies 0..14)
    top: int  # the highest number this rank has
    title: str  # "Cadet"
    next_title: str  # "" = the top rank
    progress: int  # percent to the next
    stale: bool = False  # moved on since the game last wrote it: `progress` is the least it is now
    ladder: tuple[UiRankStep, ...] = ()  # every rank of this kind, lowest first
    how: str = ""  # what moves you up
    note: str = ""  # anything else worth knowing about the ladder


@dataclass(frozen=True, slots=True)
class UiPledge:
    power: str  # "" = not pledged
    known: bool  # False = no Powerplay entry in your journals at all
    merits: int | None  # in all
    week: int  # merits earned in this Powerplay week
    week_start: str  # ISO: when this week began
    rank: int | None  # the game's own (0 = introductory missions not done); from the merits when not given
    next_rank_merits: int | None  # merits in all the next rank needs (None at rank 0)
    since: str  # ISO time of the pledge ("" = not known)


HoldStatus = Literal["idle", "running", "done", "error"]


@dataclass(frozen=True, slots=True)
class UiHold:
    """How many systems the pledged power holds, by state, and every power in all, most first (Spansh,
    asked when the tab is shown)."""

    power: str = ""
    status: HoldStatus = "idle"
    message: str = ""
    counts: tuple[tuple[str, int], ...] = ()  # ("Stronghold", 120), …
    searched: str = ""  # ISO
    powers: tuple[tuple[str, int], ...] = ()  # ("Li Yong-Rui", 2185), … all 12, most systems first


@dataclass(frozen=True, slots=True)
class UiPoint:
    day: str  # "2026-10-13"
    values: tuple[float | None, ...]  # in the order the chart names its lines


@dataclass(frozen=True, slots=True)
class UiWeek:
    start: str  # ISO: the week's start
    merits: int


@dataclass(frozen=True, slots=True)
class UiCareer:
    known: bool = False  # anything at all read yet
    since: str = ""  # the day of the oldest career entry
    standings: tuple[UiStanding, ...] = ()
    ranks: tuple[UiRank, ...] = ()  # in the game's RANKS order, those known
    pledge: UiPledge = field(default_factory=lambda: UiPledge("", False, None, 0, "", None, None, ""))
    hold: UiHold = field(default_factory=UiHold)
    standing_history: tuple[UiPoint, ...] = ()  # values in SUPERPOWERS order
    navy_history: tuple[UiPoint, ...] = ()  # values in NAVIES order, rank + progress / 100
    # the last WEEKS_SHOWN Powerplay weeks from the first with merits, oldest first (0 when none earned)
    weeks: tuple[UiWeek, ...] = ()


def standing(name: str, value: float) -> UiStanding:
    value = max(-100.0, min(100.0, value))
    i = max(i for i, (low, _) in enumerate(REPUTATION_TIERS) if value >= low)
    low, tier = REPUTATION_TIERS[i]
    if i + 1 < len(REPUTATION_TIERS):
        high, next_tier = REPUTATION_TIERS[i + 1]
    else:
        high, next_tier = 100.0, ""
    pct = 100 if not next_tier else int((value - low) / (high - low) * 100)
    to_next = round(high - value, 1) if next_tier else 0.0
    return UiStanding(name, round(value, 1), tier, next_tier, to_next, pct)


def rank_merits(rank: int) -> int:
    """Merits in all a Powerplay rank (1 or more) needs."""
    if rank <= len(PP_RANK_MERITS):
        return PP_RANK_MERITS[max(rank, 1) - 1]
    return PP_RANK_MERITS[-1] + (rank - len(PP_RANK_MERITS)) * PP_RANK_STEP


def pp_rank(merits: int) -> tuple[int, int]:
    """Powerplay rank for merits in all, and the merits in all the next rank needs (when no journal entry
    gives the rank itself)."""
    merits = max(merits, 0)
    if merits < PP_RANK_MERITS[-1]:
        rank = max(i for i, need in enumerate(PP_RANK_MERITS) if merits >= need) + 1
        return rank, PP_RANK_MERITS[rank]
    above = (merits - PP_RANK_MERITS[-1]) // PP_RANK_STEP
    rank = len(PP_RANK_MERITS) + above
    return rank, PP_RANK_MERITS[-1] + (above + 1) * PP_RANK_STEP


def thin(points: list[UiPoint], most: int = HISTORY_POINTS) -> tuple[UiPoint, ...]:
    """At most `most` points, spread evenly; the first and the newest always kept."""
    if len(points) <= most:
        return tuple(points)
    step = (len(points) - 1) / (most - 1)
    return tuple(points[round(i * step)] for i in range(most))


def ui_career(c: Career, now: datetime, hold: UiHold | None = None) -> UiCareer:
    standings = tuple(standing(n, c.standings[n]) for n in SUPERPOWERS if n in c.standings)
    ranks = []
    for kind in RANKS:
        rank = c.ranks.get(kind)
        if rank is None:
            continue
        titles = RANK_TITLES[kind]
        rank = max(0, min(rank, len(titles) - 1))
        top = rank == len(titles) - 1
        next_title = "" if top else titles[rank + 1]
        guide = RANK_GUIDE[kind]
        ladder = tuple(UiRankStep(t, guide.unlocks.get(i, "")) for i, t in enumerate(titles))
        progress = c.progress.get(kind, 0)
        ranks.append(
            UiRank(
                kind,
                rank,
                len(titles) - 1,
                titles[rank],
                next_title,
                progress,
                kind in c.stale and not top,
                ladder,
                guide.how,
                guide.note,
            )
        )
    this_week = cycle_start(now)
    earned = c.week_merits() if c.power else {}
    weeks = []
    if earned:
        # no weeks before the history knows of any merits, nor before the pledge (earned for another power)
        first = max(min(earned), max(c.pledge_weeks, default=""))
        for back in range(WEEKS_SHOWN - 1, -1, -1):
            key = _iso(this_week - timedelta(weeks=back))
            if key >= first:
                weeks.append(UiWeek(key, earned.get(key, 0)))
    rank = next_at = None
    if c.power and c.pp_rank is not None:
        rank = c.pp_rank
        # rank 0 = the introductory missions first: merits do not make rank 1
        next_at = rank_merits(rank + 1) if rank > 0 else None
    elif c.power and c.merits is not None:
        rank, next_at = pp_rank(c.merits)
    pledge = UiPledge(
        power=c.power or "",
        known=c.power is not None,
        merits=c.merits if c.power else None,
        week=weeks[-1].merits if weeks else 0,
        week_start=_iso(this_week),
        rank=rank,
        next_rank_merits=next_at,
        since=c.pledged_since if c.power else "",
    )
    return UiCareer(
        known=bool(c.first),
        since=_day(c.first),
        standings=standings,
        ranks=tuple(ranks),
        pledge=pledge,
        hold=hold if hold is not None and c.power and hold.power == c.power else UiHold(),
        standing_history=thin([UiPoint(d, v) for d, v in sorted(c.standing_days.items())]),
        navy_history=thin([UiPoint(d, v) for d, v in sorted(c.navy_days.items())]),
        weeks=tuple(weeks),
    )


def hold_message(power: str, counts: tuple[tuple[str, int], ...]) -> str:
    total = sum(n for _, n in counts)
    capped = any(n >= 10_000 for _, n in counts)
    return f"{power} holds {total:,}{' or more' if capped else ''} systems"
