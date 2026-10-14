"""Typed scouting requests and results. Pure data: the sources layer fills them, core and UI read them.

A "scout" is one Spansh tour job: valuable systems within `radius_ly` of the straight from→to line,
ordered into a tour. `riches` finds FSS/DSS value, `bio` finds exobiology. The first and last rows of a
result are the from/to systems themselves (no bodies).
id64 values are ints here; they become strings only at the JSON edge.
"""

from dataclasses import dataclass
from typing import Literal

ScoutKind = Literal["riches", "bio"]


@dataclass(frozen=True)
class ScoutQuery:
    from_system: str
    to_system: str
    range_ly: float
    radius_ly: float = 25.0
    max_results: int = 10
    max_distance_ls: int = 50_000
    min_value: int = 1_000_000
    use_mapping_value: bool = True  # riches only: rank by scan + mapping value
    loop: bool = False
    avoid_thargoids: bool = True


@dataclass(frozen=True)
class SystemHit:
    """One system-name suggestion."""

    id64: int
    name: str
    x: float
    y: float
    z: float


@dataclass(frozen=True)
class BioSpecies:
    genus: str  # e.g. "Frutexa"
    species: str  # e.g. "Frutexa Acus"
    value: int  # base sale value, Cr (no first-logged bonus)
    count: int  # how many sightings Spansh knows of on this body


@dataclass(frozen=True)
class ScoutBody:
    id64: int
    name: str
    type: str  # "Planet" / "Star"
    subtype: str  # e.g. "High metal content world"
    distance_ls: float
    scan_value: int
    mapping_value: int
    terraformable: bool
    species: tuple[BioSpecies, ...] = ()

    @property
    def bio_value(self) -> int:
        """Sum of species values (Spansh's own per-body total is not trusted)."""
        return sum(s.value for s in self.species)


@dataclass(frozen=True)
class ScoutSystem:
    id64: int
    name: str
    jumps: int  # jumps from the previous row, as Spansh plotted them
    x: float
    y: float
    z: float
    bodies: tuple[ScoutBody, ...]


@dataclass(frozen=True)
class ScoutResult:
    kind: ScoutKind
    query: ScoutQuery
    systems: tuple[ScoutSystem, ...]
    from_cache: bool = False
