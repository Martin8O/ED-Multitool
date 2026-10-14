"""Jump range from FSD constants and ship mass.

    range = opt_mass / total_mass * (fuel_used / fuel_mul) ** (1 / fuel_power) + guardian_boost

total_mass = unladen + cargo + fuel in the tanks; fuel_used = min(fuel in main tank, max fuel per jump).
The game's Loadout.MaxJumpRange is the special case cargo = 0 and fuel in tank = max fuel per jump.
"""

from edmultitool.core.events import FsdFit


def jump_range(
    fsd: FsdFit,
    *,
    unladen_mass: float,
    cargo: float = 0.0,
    fuel_main: float,
    fuel_reservoir: float = 0.0,
    guardian_boost_ly: float = 0.0,
) -> float:
    """Range of ONE jump in ly with the given mass and fuel; 0 when there is no fuel to jump."""
    spec = fsd.spec
    fuel_used = min(fuel_main, spec.max_fuel)
    if fuel_used <= 0:
        return 0.0
    total_mass = unladen_mass + cargo + fuel_main + fuel_reservoir
    base = spec.opt_mass / total_mass * float((fuel_used / spec.fuel_mul) ** (1.0 / spec.fuel_power))
    # Guardian booster: added as the flat in-game stat. NOT yet validated against a journal with a
    # booster fitted (none seen so far) — revisit when such a Loadout shows up.
    return base + guardian_boost_ly


def best_case_range(fsd: FsdFit, *, unladen_mass: float, guardian_boost_ly: float = 0.0) -> float:
    """What the game reports as Loadout.MaxJumpRange: no cargo, just one jump's fuel in the tank."""
    return jump_range(
        fsd,
        unladen_mass=unladen_mass,
        fuel_main=fsd.spec.max_fuel,
        guardian_boost_ly=guardian_boost_ly,
    )
