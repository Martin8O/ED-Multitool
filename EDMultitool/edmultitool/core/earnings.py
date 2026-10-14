"""What you earned and spent — this session, today, this week — and the data you still carry unsold.
Pure; folded from the journal only, no online source.

Like `Career`, `Earnings` is changed in place: the history pass folds every journal oldest first, then hands
the object to the main thread, which keeps folding live entries into it.

Carried data:
- cartographic: every star / planet scanned, valued with `arrival.sale_value` (first discovery, first mapped
  and probe-efficiency bonuses as the scan tells them), plus what each system's other bodies add to its sale
  (`arrival.honk_value`; bodies only counted by the discovery scan at the 500 minimum, and nothing for them
  where you did not honk); a sale clears the systems it names, by name;
- exobiology: every species analysed (third sample), at its base value (`catalog.species`): the journal does
  not say whether you are the first to log it, so that bonus is never counted; a sale clears one of each
  species sold.
A death loses both.

Balance over time: exact at every login (LoadGame `Credits`), followed between logins by every credit earned,
spent, or paid for cargo and got back for it (checked against the next login's exact figure: off by a few
credits at most on a real journal); the live balance ends the line.

"Today" starts at your local midnight, "this week" on Monday 00:00 local; `now` carries the local zone.
"""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

from edmultitool.catalog.species import species_value
from edmultitool.core.arrival import honk_value, sale_value
from edmultitool.core.events import (
    EARN_KINDS,
    SPEND_KINDS,
    BalanceKnown,
    BodyScanned,
    CashMoved,
    DataMapped,
    DataScanned,
    DataSurveyed,
    Died,
    ExplorationSold,
    MoneyEvent,
    MoneyMoved,
    OrganicSold,
    Refined,
    SessionEnded,
    SessionStarted,
    SpeciesAnalysed,
)
from edmultitool.core.explored import species_key

EARN_LABELS = {
    "exploration": "Exploration data",
    "exobiology": "Exobiology",
    "trade": "Trade profit",
    "bounties": "Bounties & combat bonds",
    "missions": "Missions",
    "other": "Other",
}
SPEND_LABELS = {
    "repairs": "Repairs",
    "fuel": "Fuel",
    "rearm": "Ammo & restock",
    "ships": "Ships & modules",
    "fines": "Fines & bounties paid",
    "rebuy": "Insurance (rebuy)",
    "other": "Other",
}
BALANCE_POINTS = 300  # at most this many points on the balance chart
MIN_HOURS = 0.1  # credits per hour need at least six minutes of play
MINUS = "\N{MINUS SIGN}"


def _parse(when: str) -> datetime | None:
    try:
        t = datetime.fromisoformat(when.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


@dataclass(slots=True)
class _Body:
    system: str
    scan: BodyScanned
    mapped: bool = False
    efficient: bool = False
    when: str = ""
    mapping_only: bool = False  # its scan data was sold before: only the mapping is left to sell

    def value(self) -> int:
        if self.mapping_only:
            return sale_value(self.scan, True, self.efficient) - sale_value(self.scan, False, False)
        return sale_value(self.scan, self.mapped, self.efficient)


@dataclass(slots=True)
class Earnings:
    moves: list[MoneyMoved] = field(default_factory=list)  # every credit earned or spent, oldest first
    session_start: str = ""  # the last LoadGame
    session_end: str = ""  # the last Shutdown (older than session_start = the game is still running)
    bodies: dict[tuple[int, int], _Body] = field(default_factory=dict)  # (system id64, BodyID) → unsold data
    surveyed: dict[int, tuple[str, int]] = field(default_factory=dict)  # system id64 → (name, body count)
    # what you sold already: Universal Cartographics buys a body's data (scan, then mapping) and a system's
    # discovery scan once. sold_bodies: body → its mapping was sold too
    sold_bodies: dict[tuple[int, int], bool] = field(default_factory=dict)
    sold_systems: set[int] = field(default_factory=set)
    _sold_scans: dict[tuple[int, int], tuple[str, BodyScanned]] = field(default_factory=dict)  # scanned again
    _remapped: dict[tuple[int, int], bool] = field(default_factory=dict)  # mapped again before its new scan
    species: list[tuple[str, str]] = field(default_factory=list)  # (species name, when) analysed, unsold
    refined: list[Refined] = field(default_factory=list)  # every tonne refined this session, oldest first
    died: str = ""  # the last death
    lost: int = 0  # what that death cost in unsold data, Cr (estimated)
    last: str = ""  # the newest entry folded
    latest: str = ""  # the newest journal entry of any kind (the session's length runs to it)
    last_seen: Counter[MoneyEvent] = field(default_factory=Counter)  # the entries folded in that second
    balance: list[tuple[str, int]] = field(default_factory=list)  # (when, credits) after each change
    _balance: int | None = None  # None until the first login gives the exact figure
    live: tuple[str, int] | None = None  # (when, credits) the game's live status said last
    _value: int | None = None  # data_value() until the carried data changes (the page asks on every refresh)

    def add(self, event: MoneyEvent) -> None:
        when = event.when
        if when > self.last:
            self.last, self.last_seen = when, Counter()
        self.saw(when)
        if when == self.last:
            self.last_seen[event] += 1
        match event:
            case MoneyMoved():
                self.moves.append(event)
                self._credits(event.amount if event.earned else -event.amount, when)
            case CashMoved():
                self._credits(event.amount, when)
            case BalanceKnown():
                self._balance = event.credits
                self.balance.append((when, event.credits))
            case SessionStarted():
                self.session_start = when
                self.refined = []  # only this session's tonnes are ever shown
            case SessionEnded():
                self.session_end = when
            case DataScanned():
                key = (event.body.system_address, event.body.body_id)
                old = self.bodies.get(key)
                if old is None and key in self.sold_bodies:  # sold once: only an unsold mapping can count
                    self._sold_scans[key] = (event.system, event.body)
                    if key in self._remapped:
                        self._map_again(key, self._remapped.pop(key), when)
                elif old is None:
                    self.bodies[key] = _Body(event.system, event.body, when=when)
                else:  # scanned again: the newer scan, the mapping kept
                    old.system, old.scan = event.system, event.body
            case DataSurveyed():
                if event.system_address not in self.sold_systems:
                    self.surveyed[event.system_address] = (event.system, event.body_count)
            case DataMapped():
                key = (event.system_address, event.body_id)
                body = self.bodies.get(key)
                if body is not None:
                    body.mapped, body.efficient = True, event.efficient
                elif self.sold_bodies.get(key) is False:  # its scan sold, its mapping not
                    if key in self._sold_scans:
                        self._map_again(key, event.efficient, when)
                    else:  # the game writes the new scan just after
                        self._remapped[key] = event.efficient
            case SpeciesAnalysed():
                self.species.append((event.species, when))
            case Refined():
                self.refined.append(event)
            case ExplorationSold():
                sold = set(event.systems)
                gone = [k for k, b in self.bodies.items() if b.system in sold]
                gone_systems = [k for k, s in self.surveyed.items() if s[0] in sold]
                for key in gone:
                    self.sold_bodies[key] = self.bodies.pop(key).mapped
                for address in gone_systems:
                    del self.surveyed[address]
                self.sold_systems.update(address for address, _ in gone)
                self.sold_systems.update(gone_systems)
            case OrganicSold():
                left = Counter(species_key(s) for s in event.species)
                kept = []
                for name, at in self.species:
                    if left[species_key(name)] > 0:
                        left[species_key(name)] -= 1
                    else:
                        kept.append((name, at))
                self.species = kept
            case Died():
                self.died = when
                self.lost = self.data_value() + sum(species_value(n) or 0 for n, _ in self.species)
                self.bodies, self.surveyed, self.species = {}, {}, []
                self._remapped = {}
        if isinstance(event, DataScanned | DataSurveyed | DataMapped | ExplorationSold | Died):
            self._value = None  # the carried data changed

    def _credits(self, change: int, when: str) -> None:
        if self._balance is not None:
            self._balance += change
            self.balance.append((when, self._balance))

    def _map_again(self, key: tuple[int, int], efficient: bool, when: str) -> None:
        system, scan = self._sold_scans.pop(key)
        self.bodies[key] = _Body(system, scan, True, efficient, when, mapping_only=True)

    def saw(self, when: str) -> None:
        """Any journal entry: the game was running at `when`."""
        self.latest = max(self.latest, when)

    def add_live(self, events: list[MoneyEvent]) -> None:
        """Entries that came live while the history pass ran: those the pass already folded (up to its newest
        second) are not counted twice."""
        seen = Counter(self.last_seen)
        last = self.last
        for event in events:
            if event.when < last:
                continue
            if event.when == last and seen[event] > 0:
                seen[event] -= 1
                continue
            self.add(event)

    def data_value(self) -> int:
        if self._value is None:
            systems: dict[int, list[_Body]] = {}
            for (address, _), b in self.bodies.items():
                systems.setdefault(address, []).append(b)
            self._value = sum(self._system_value(address, found) for address, found in systems.items())
        return self._value

    def _system_value(self, address: int, found: list[_Body]) -> int:
        """The scanned bodies, then what every body but the arrival star adds, scanned or only counted."""
        value = sum(b.value() for b in found)
        found = [b for b in found if not b.mapping_only]  # their discovery value was sold with their scan
        if not found:
            return value
        nearest = min(found, key=lambda b: (b.scan.distance_ls, b.scan.body_id))
        arrival = nearest if nearest.scan.distance_ls == 0 else None  # None: the arrival star not scanned
        value += sum(honk_value(b.scan) for b in found if b is not arrival)
        _, count = self.surveyed.get(address, ("", 0))
        unscanned = count - len(found) - (1 if arrival is None else 0)
        return value + max(0, unscanned) * honk_value(None)


@dataclass(frozen=True, slots=True)
class UiMoney:
    """One row of the card: a kind with its sum in each window, Cr."""

    kind: str  # the journal-side key ("exploration", "repairs"… or "total")
    label: str
    session: int = 0
    today: int = 0
    week: int = 0


@dataclass(frozen=True, slots=True)
class UiCarriedSpecies:
    name: str
    value: int | None  # base value, Cr; None = not in the catalog


@dataclass(frozen=True, slots=True)
class UiBalance:
    when: str  # ISO
    credits: int


@dataclass(frozen=True, slots=True)
class UiEarnings:
    known: bool = False  # the journals were read
    session_start: str = ""  # ISO; "" = no game start seen
    session_hours: float = 0.0  # from the game's start to its newest journal entry
    running: bool = False  # no Shutdown since the start (the game runs, or it stopped without one)
    per_hour: int | None = None  # earned this session per hour of it; None = too short
    week_start: str = ""  # ISO, local Monday 00:00
    earned: tuple[UiMoney, ...] = ()  # EARN_KINDS order, then "total"
    spent: tuple[UiMoney, ...] = ()  # SPEND_KINDS order, then "total"
    net: UiMoney = field(default_factory=lambda: UiMoney("net", "Net"))
    data_value: int = 0  # unsold cartographic data, Cr (estimated)
    data_bodies: int = 0
    data_systems: int = 0
    data_since: str = ""  # the oldest unsold scan, ISO
    bio_value: int = 0  # unsold exobiology at base value, Cr
    bio_species: tuple[UiCarriedSpecies, ...] = ()  # newest first
    bio_since: str = ""
    died: str = ""  # the last death, ISO (shown when it was this week)
    lost: int = 0  # what it cost in unsold data, Cr
    balance: tuple[UiBalance, ...] = ()  # your balance over time, oldest first (thinned), the live one last
    text: str = "—"  # the EDMC panel's Earned: row


def _rows(moves: list[tuple[MoneyMoved, bool, bool, bool]], earned: bool) -> tuple[UiMoney, ...]:
    """Sums per kind and in all; each move comes with whether it falls in the session, today, this week."""
    kinds, labels = (EARN_KINDS, EARN_LABELS) if earned else (SPEND_KINDS, SPEND_LABELS)
    sums = {k: [0, 0, 0] for k in (*kinds, "total")}
    for m, *inside in moves:
        if m.earned != earned:
            continue
        for key in (m.kind if m.kind in sums else "other", "total"):
            for i, yes in enumerate(inside):
                sums[key][i] += m.amount if yes else 0
    return tuple(UiMoney(k, labels.get(k, "Total"), *sums[k]) for k in (*kinds, "total"))


def ui_earnings(
    e: Earnings,
    now: datetime,
    known: bool = True,
    local: Callable[[datetime], datetime] | None = None,
) -> UiEarnings:
    """`now` is aware, in the player's local time; `local` turns a local wall-clock time into an aware one
    with the offset in force then (a midnight before a clock change has its own), default: `now`'s offset."""
    attach = local or (lambda t: t.replace(tzinfo=now.tzinfo))
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    day = attach(midnight)
    week = attach(midnight - timedelta(days=midnight.weekday()))
    start = _parse(e.session_start) if e.session_start else None
    oldest = min(week, start) if start is not None else week
    moves: list[tuple[MoneyMoved, bool, bool, bool]] = []
    for m in reversed(e.moves):
        t = _parse(m.when)
        if t is None:
            continue
        if t < oldest:
            break  # older than every window (moves are in time order)
        moves.append((m, start is not None and t >= start, t >= day, t >= week))
    earned, spent = _rows(moves, True), _rows(moves, False)
    et, st = earned[-1], spent[-1]
    net = UiMoney("net", "Net", et.session - st.session, et.today - st.today, et.week - st.week)
    running = bool(e.session_start) and e.session_end < e.session_start
    end = _parse(e.latest) if e.latest else None
    hours = max(0.0, (end - start).total_seconds() / 3600) if start is not None and end is not None else 0.0
    bio = tuple(UiCarriedSpecies(n, species_value(n)) for n, _ in reversed(e.species))
    died = _parse(e.died) if e.died else None
    recent_death = died is not None and died >= week
    ui = UiEarnings(
        known=known,
        session_start=e.session_start,
        session_hours=round(hours, 2),
        running=running,
        per_hour=round(et.session / hours) if hours >= MIN_HOURS else None,
        week_start=week.isoformat(timespec="seconds"),
        earned=earned,
        spent=spent,
        net=net,
        data_value=e.data_value(),
        data_bodies=len(e.bodies),
        data_systems=len({b.system for b in e.bodies.values()}),
        data_since=min((b.when for b in e.bodies.values()), default=""),
        bio_value=sum(s.value or 0 for s in bio),
        bio_species=bio,
        bio_since=min((w for _, w in e.species), default=""),
        died=e.died if recent_death else "",
        lost=e.lost if recent_death else 0,
        balance=_balance_points(e),
    )
    return replace(ui, text=_text(ui)) if known else ui


def _balance_points(e: Earnings) -> tuple[UiBalance, ...]:
    points = [UiBalance(when, credits) for when, credits in e.balance]
    if e.live is not None and points and e.live[0] > points[-1].when and e.live[1] != points[-1].credits:
        points.append(UiBalance(*e.live))  # the live balance, newer than the journal's last change
    if len(points) <= BALANCE_POINTS:
        return tuple(points)
    step = (len(points) - 1) / (BALANCE_POINTS - 1)  # spread evenly; the first and the newest kept
    return tuple(points[round(i * step)] for i in range(BALANCE_POINTS))


def _text(ui: UiEarnings) -> str:
    parts = []
    if ui.session_start:
        parts.append(f"session {_signed(ui.net.session)}")
    parts.append(f"today {_signed(ui.net.today)}")
    unsold = ui.data_value + ui.bio_value
    if unsold:
        parts.append(f"unsold {_m(unsold)}")
    return " · ".join(parts)


def _m(n: int) -> str:
    return f"{n / 1e6:.1f} M"


def _signed(n: int) -> str:
    return f"{'+' if n >= 0 else MINUS}{_m(abs(n))}"
