"""Your money and legal standing, folded from the journal and Status.json. Pure and immutable.

Unpaid fines and bounties are worked out from the journal: every CommitCrime adds to its faction, PayFines /
PayBounties / PayLegacyFines settle. Only crimes since your ship was last destroyed count: a destruction by
the authorities usually settles them with the rebuy, and the journal does not say which ones it did. Bounties
do not expire in the current game, so nothing else drops them.
"""

from dataclasses import dataclass, replace

from edmultitool.core.events import (
    CommanderEvent,
    CreditsKnown,
    CrimeCommitted,
    DebtPaid,
    Died,
    LegalStateKnown,
    NotorietyKnown,
)


@dataclass(frozen=True, slots=True)
class Debt:
    faction: str
    fines: int = 0  # credits owed in fines
    bounties: int = 0  # credits on your head


@dataclass(frozen=True, slots=True)
class Commander:
    credits: int | None = None
    legal: str = ""  # Status LegalState in the system you are in ("" = not known)
    notoriety: int | None = None  # at the last login
    debts: tuple[Debt, ...] = ()  # since the last destruction, in the order the crimes came


def apply_commander(c: Commander, event: CommanderEvent) -> Commander:
    match event:
        case CreditsKnown():
            return replace(c, credits=event.credits)
        case LegalStateKnown():
            return replace(c, legal=event.state)
        case NotorietyKnown():
            return replace(c, notoriety=event.level)
        case CrimeCommitted():
            debts = list(c.debts)
            for i, d in enumerate(debts):
                if d.faction == event.faction:
                    debts[i] = Debt(d.faction, d.fines + event.fine, d.bounties + event.bounty)
                    break
            else:
                debts.append(Debt(event.faction, event.fine, event.bounty))
            return replace(c, debts=tuple(debts))
        case DebtPaid():
            kept = []
            for d in c.debts:
                if event.faction in ("", d.faction):
                    d = Debt(d.faction, 0, 0 if event.bounties else d.bounties)
                if d.fines or d.bounties:
                    kept.append(d)
            return replace(c, debts=tuple(kept))
        case Died():
            return replace(c, debts=())
    return c


@dataclass(frozen=True, slots=True)
class UiWarning:
    level: str  # "danger" (red) / "warn" (amber)
    text: str  # the chip, e.g. "Wanted here"
    detail: str = ""  # hover text


@dataclass(frozen=True, slots=True)
class UiCommander:
    credits: float | None = None  # millions, 1 decimal place
    warnings: tuple[UiWarning, ...] = ()


# Status LegalState → the chip; Clean / Allied / Thargoid / unknown say nothing.
_LEGAL = {
    "Wanted": ("danger", "Wanted here", "You have a bounty in this system: the authorities will attack you."),
    "Hostile": ("danger", "Hostile here", "The system's authorities are hostile to you."),
    "Warrant": (
        "warn",
        "Warrant here",
        "A bounty on you is known in this jurisdiction: a scan here reveals it.",
    ),
    "IllegalCargo": ("warn", "Illegal cargo", "Your cargo is illegal in this system: avoid being scanned."),
    "Speeding": ("warn", "Speeding", "Too fast near a station: you will be fined."),
    "PassengerWanted": ("warn", "Wanted passenger", "A passenger aboard is wanted in this system."),
}


def _cr(n: int) -> str:
    return f"{n:,} Cr"


def ui_commander(c: Commander) -> UiCommander:
    warnings: list[UiWarning] = []
    if c.legal in _LEGAL:
        level, text, detail = _LEGAL[c.legal]
        warnings.append(UiWarning(level, text, detail))
    if c.notoriety:
        warnings.append(
            UiWarning(
                "danger" if c.notoriety >= 5 else "warn",
                f"Notoriety {c.notoriety}",
                "As at your last login (0-10). It falls over time; while above 0, Interstellar Factors will "
                "not clear your bounties.",
            )
        )
    fines = sum(d.fines for d in c.debts)
    bounties = sum(d.bounties for d in c.debts)
    if fines or bounties:
        parts = [
            p
            for p in (f"{_cr(bounties)} bounties" if bounties else "", f"{_cr(fines)} fines" if fines else "")
            if p
        ]
        lines = [
            f"{d.faction}: "
            + ", ".join(
                p
                for p in (
                    f"bounty {_cr(d.bounties)}" if d.bounties else "",
                    f"fine {_cr(d.fines)}" if d.fines else "",
                )
                if p
            )
            for d in c.debts
        ]
        warnings.append(
            UiWarning(
                "danger" if bounties else "warn",
                "Unpaid: " + " · ".join(parts),
                "\n".join(lines) + "\nFrom your journals, since your ship was last destroyed.",
            )
        )
    credits = None if c.credits is None else round(c.credits / 1_000_000, 1)
    return UiCommander(credits=credits, warnings=tuple(warnings))
