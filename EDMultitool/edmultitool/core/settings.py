"""User settings, stored as JSON in the plugin's own data folder (never in EDMC's config on C:)."""

import json
import os
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Literal

from edmultitool.core.plot import PLOT_KINDS, PlotKind

RouteBasis = Literal["full", "now", "best"]
FollowBy = Literal["position", "stops"]
FOLLOW_BY: tuple[FollowBy, ...] = ("position", "stops")
ROUTE_BASES: tuple[RouteBasis, ...] = ("full", "now", "best")
ROUTE_BASIS_LABELS: dict[RouteBasis, str] = {
    "full": "full tank",
    "now": "current fuel & cargo",
    "best": "best case (empty)",
}
MAX_MARGIN_PCT = 50.0
MAX_NAME_LEN = 100
MAX_CREDITS = 10_000_000_000
MAX_RECENT = 8  # destinations remembered for the To field

# which planets the scan search keeps: any, or only the prime ones — Earth-like, water and ammonia worlds
# plus every terraformable (the "ELW hunt" preset). Spansh's riches search cannot ask for a type.
PlanetFilter = Literal["any", "prime"]
PLANET_FILTERS: tuple[PlanetFilter, ...] = ("any", "prime")


@dataclass(frozen=True, slots=True)
class ScoutPrefs:
    """The route-scout form, remembered between runs. Every threshold of 0 means "off"."""

    from_system: str = ""  # "" = start from the current system
    to_system: str = ""
    radius_ly: float = 25.0  # how far a stop may lie from the straight from→to line
    max_distance_ls: int = 50_000  # how far a body may lie from the arrival star
    max_stops: int = 10  # asked of each search (scan and bio), so up to twice this many stops
    riches: bool = True  # search FSS/DSS value
    bio: bool = True  # search exobiology; neither of the two = plot the route only
    min_scan: int = 0  # Cr per body, FSS scan value
    min_mapping: int = 1_000_000  # Cr per body, DSS mapping value
    min_species: int = 0  # Cr per species; cheaper species are hidden and not counted
    min_bio_body: int = 1_000_000  # Cr per body, sum of the species that are counted
    plotter: PlotKind = "exact"  # route only: "exact" (normal jumps) or "neutron" (via neutron stars)
    # a stop whose bodies are all yours already is swapped for another only if that adds at most this
    # much to the trip; 0 = never swap
    max_detour_ly: float = 25.0
    planets: PlanetFilter = "any"  # scan search only; the bio search is not filtered


_DEFAULT_SCOUT = ScoutPrefs()
# each field's kind (str / bool / int / float), read off its default value
_KINDS: dict[str, type] = {f.name: type(getattr(_DEFAULT_SCOUT, f.name)) for f in fields(ScoutPrefs)}
# text fields with a fixed set of values; other text fields are names, checked for length only
_CHOICES: dict[str, tuple[str, ...]] = {"plotter": PLOT_KINDS, "planets": PLANET_FILTERS}
# field → (min, max) for numbers
_LIMITS: dict[str, tuple[float, float]] = {
    "radius_ly": (1, 1000),
    "max_distance_ls": (1, 1_000_000),
    "max_stops": (1, 100),
    "min_scan": (0, MAX_CREDITS),
    "min_mapping": (0, MAX_CREDITS),
    "min_species": (0, MAX_CREDITS),
    "min_bio_body": (0, MAX_CREDITS),
    "max_detour_ly": (0, 1000),
}


@dataclass(frozen=True, slots=True)
class Settings:
    route_basis: RouteBasis = "full"  # which jump range routes are planned with
    margin_pct: float = 0.0  # safety margin taken off that range, 0..50 %
    scout: ScoutPrefs = field(default_factory=ScoutPrefs)
    recent_to: tuple[str, ...] = ()  # destinations scouted, newest first, at most MAX_RECENT
    flash: bool = True  # the page flashes on arriving where one body is worth at least `flash_over`
    flash_over: int = 30_000_000  # Cr
    update_check: bool = True  # ask GitHub about once a day whether a newer version is out
    follow_by: FollowBy = "position"  # stops flown past count as behind you; "stops": only those reached


def remember_destination(recent: tuple[str, ...], name: str) -> tuple[str, ...]:
    """`name` moved (or added) to the front; the same name in other letter case counts once."""
    name = name.strip()
    if not name:
        return recent
    return (name, *(r for r in recent if r.casefold() != name.casefold()))[:MAX_RECENT]


def _normalise_recent(raw: object) -> tuple[str, ...]:
    out: tuple[str, ...] = ()
    if isinstance(raw, list):
        for name in reversed(raw[:MAX_RECENT]):
            if isinstance(name, str) and len(name) <= MAX_NAME_LEN:
                out = remember_destination(out, name)
    return out


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value != value:  # NaN
        return None
    return float(value)


def parse_scout_prefs(raw: object) -> ScoutPrefs | str:
    """Strict: a scout form sent by the page → ScoutPrefs, or a message naming the first bad field."""
    if not isinstance(raw, dict):
        return "the scout form must be a JSON object"
    out: dict[str, object] = {}
    for name, kind in _KINDS.items():
        if name not in raw:
            return f"missing field '{name}'"
        value = raw[name]
        if name in _CHOICES:
            if value not in _CHOICES[name]:
                return f"'{name}' must be one of: {', '.join(_CHOICES[name])}"
            out[name] = value
        elif kind is str:
            if not isinstance(value, str) or len(value.strip()) > MAX_NAME_LEN:
                return f"'{name}' must be text of at most {MAX_NAME_LEN} characters"
            out[name] = value.strip()
        elif kind is bool:
            if not isinstance(value, bool):
                return f"'{name}' must be true or false"
            out[name] = value
        else:
            lo, hi = _LIMITS[name]
            num = _number(value)
            if num is None or not lo <= num <= hi or (kind is int and num != int(num)):
                whole = "whole " if kind is int else ""
                return f"'{name}' must be a {whole}number from {lo:g} to {hi:g}"
            out[name] = int(num) if kind is int else num
    prefs = ScoutPrefs(**out)  # type: ignore[arg-type]
    if not prefs.to_system:
        return "choose a destination"
    return prefs


def _normalise_scout(raw: object) -> ScoutPrefs:
    """Lenient: whatever is stored → ScoutPrefs, each broken value falling back to its default."""
    if not isinstance(raw, dict):
        return ScoutPrefs()
    out: dict[str, object] = {}
    for name, kind in _KINDS.items():
        value = raw.get(name)
        if name in _CHOICES:
            ok = value in _CHOICES[name]
        elif kind is str:
            ok = isinstance(value, str) and len(value) <= MAX_NAME_LEN
        elif kind is bool:
            ok = isinstance(value, bool)
        else:
            lo, hi = _LIMITS[name]
            num = _number(value)
            ok = num is not None and lo <= num <= hi and (kind is not int or num == int(num))
            if ok and num is not None:
                value = int(num) if kind is int else num
        out[name] = value if ok else getattr(_DEFAULT_SCOUT, name)
    return ScoutPrefs(**out)  # type: ignore[arg-type]


FLASH_LIMITS = (1_000_000, 1_000_000_000)  # Cr


def parse_flash(raw: object) -> tuple[bool, int] | str:
    """POST /api/flash: {"on": bool, "over": Cr} from the "This system" card."""
    if not isinstance(raw, dict) or not isinstance(raw.get("on"), bool):
        return "on must be true or false"
    over = _number(raw.get("over"))
    lo, hi = FLASH_LIMITS
    if over is None or not lo <= over <= hi:
        return f"over must be from {lo // 1_000_000} to {hi // 1_000_000} million Cr"
    return bool(raw["on"]), int(over)


def parse_update_check(raw: object) -> bool | str:
    """POST /api/update-check: {"on": bool} from the page's footer."""
    if not isinstance(raw, dict) or not isinstance(raw.get("on"), bool):
        return "on must be true or false"
    return bool(raw["on"])


def normalise(raw: dict[str, object]) -> Settings:
    """Anything → valid Settings; unknown or broken values fall back to defaults."""
    basis = raw.get("route_basis")
    margin = _number(raw.get("margin_pct"))
    flash, over = raw.get("flash"), _number(raw.get("flash_over"))
    check = raw.get("update_check")
    by = raw.get("follow_by")
    return Settings(
        route_basis=basis if basis in ROUTE_BASES else "full",
        margin_pct=min(MAX_MARGIN_PCT, max(0.0, margin)) if margin is not None else 0.0,
        scout=_normalise_scout(raw.get("scout")),
        recent_to=_normalise_recent(raw.get("recent_to")),
        flash=flash if isinstance(flash, bool) else True,
        flash_over=int(over)
        if over is not None and FLASH_LIMITS[0] <= over <= FLASH_LIMITS[1]
        else 30_000_000,
        update_check=check if isinstance(check, bool) else True,
        follow_by=by if by in FOLLOW_BY else "position",
    )


def settings_from_form(basis_label: str, margin_text: str, current: Settings) -> Settings:
    """Settings-tab values -> Settings. Unknown label / unparsable margin keep the current value."""
    by_label = {label: basis for basis, label in ROUTE_BASIS_LABELS.items()}
    try:
        margin: float = float(margin_text.strip().replace(",", "."))
    except ValueError:
        margin = current.margin_pct
    if margin != margin:  # NaN
        margin = current.margin_pct
    checked = normalise({"route_basis": by_label.get(basis_label, current.route_basis), "margin_pct": margin})
    return replace(current, route_basis=checked.route_basis, margin_pct=checked.margin_pct)


def load_settings(path: Path) -> Settings:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Settings()
    return normalise(raw) if isinstance(raw, dict) else Settings()


def save_settings(path: Path, settings: Settings) -> None:
    """Atomic write: a crash mid-save never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(settings), indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)
