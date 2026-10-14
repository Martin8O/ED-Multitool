"""Route-only plots: typed requests and results for Spansh's galaxy plotter. Pure data plus the ship-build
mapping; the sources layer fills them, the view reads them.

Both kinds are plotted jump by jump from the real ship build (FSD constants, mass, tanks, cargo) with fuel
modelled along the way — the same table either way:
- `exact`: normal jumps only;
- `neutron`: neutron supercharges allowed where they pay off.
The first waypoint is the start itself (leg 0, jumps 0); the last one is the destination.
"""

from dataclasses import dataclass
from typing import Literal

from edmultitool.catalog.fsd import supercharge_multiplier
from edmultitool.core.events import LoadoutChanged

PlotKind = Literal["exact", "neutron"]
PLOT_KINDS: tuple[PlotKind, ...] = ("exact", "neutron")


@dataclass(frozen=True, slots=True)
class ShipBuild:
    """What Spansh's galaxy plotter needs to model every jump; mirrors its own journal-Loadout import."""

    optimal_mass: float  # t, after engineering
    max_fuel_per_jump: float  # t, after engineering
    fuel_multiplier: float  # e.g. 0.013 (the FSD "linear constant" / 1000)
    fuel_power: float  # e.g. 2.45
    base_mass: float  # t: unladen mass + reservoir fuel (Spansh's own Loadout import does the same)
    tank_size: float  # t, main tank
    reservoir_size: float  # t
    range_boost: float  # ly, Guardian FSD booster
    supercharge_multiplier: int  # 4, or 6 for the SCO Mk II drive


def ship_build(ship: LoadoutChanged) -> ShipBuild | None:
    """The plotter's ship build from a journal Loadout; None when the FSD is unknown."""
    fsd = ship.fsd
    if fsd is None:
        return None
    return ShipBuild(
        optimal_mass=fsd.spec.opt_mass,
        max_fuel_per_jump=fsd.spec.max_fuel,
        fuel_multiplier=fsd.spec.fuel_mul,
        fuel_power=fsd.spec.fuel_power,
        base_mass=ship.unladen_mass + ship.fuel_capacity_reserve,
        tank_size=ship.fuel_capacity_main,
        reservoir_size=ship.fuel_capacity_reserve,
        range_boost=ship.guardian_boost_ly,
        supercharge_multiplier=supercharge_multiplier(fsd.item),
    )


@dataclass(frozen=True, slots=True)
class PlotQuery:
    source_id64: int
    destination_id64: int
    ship: ShipBuild
    cargo: int  # t carried the whole way
    neutrons: bool = False  # allow neutron supercharges

    @property
    def kind(self) -> PlotKind:
        return "neutron" if self.neutrons else "exact"


@dataclass(frozen=True, slots=True)
class Waypoint:
    id64: int
    name: str
    x: float
    y: float
    z: float
    leg_ly: float  # distance from the previous waypoint (0 for the start)
    jumps: int  # jumps from the previous waypoint: 1 each (0 for the start)
    neutron: bool  # supercharge at this neutron star: the NEXT jump is the boosted one
    refuel: bool  # fill up here (always a scoopable star)
    scoopable: bool | None  # the arrival star can be scooped (K, G, B, F, O, A, M); None = not known


@dataclass(frozen=True, slots=True)
class PlotResult:
    kind: PlotKind
    waypoints: tuple[Waypoint, ...]
    from_cache: bool = False

    @property
    def total_jumps(self) -> int:
        return sum(w.jumps for w in self.waypoints)
