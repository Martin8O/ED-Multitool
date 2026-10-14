"""Frame Shift Drive constants (game facts).

Jump range formula (see `edmultitool.core.jumprange`):
    range = opt_mass / total_mass * (fuel / fuel_mul) ** (1 / fuel_power)   [+ Guardian booster]

`rating` is the letter grade; journal items encode it as class1..class5 = E..A.
Validated against real Loadout.MaxJumpRange values (tests/data/loadouts.json).
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FsdSpec:
    opt_mass: float  # t
    max_fuel: float  # t per jump
    fuel_mul: float  # "linear constant" / 1000
    fuel_power: float  # "power constant", depends on size only


RATINGS = "EDCBA"  # journal class1..class5

# Power constant by drive size (same for standard and Supercruise Overcharge drives).
_FUEL_POWER = {2: 2.0, 3: 2.15, 4: 2.3, 5: 2.45, 6: 2.6, 7: 2.75, 8: 2.9}

# Standard drives: size -> (opt_mass, max_fuel) per rating E, D, C, B, A.
_STANDARD: dict[int, tuple[tuple[float, float], ...]] = {
    2: ((48, 0.6), (54, 0.6), (60, 0.6), (75, 0.8), (90, 0.9)),
    3: ((80, 1.2), (90, 1.2), (100, 1.2), (125, 1.5), (150, 1.8)),
    4: ((280, 2.0), (315, 2.0), (350, 2.0), (437.5, 2.5), (525, 3.0)),
    5: ((560, 3.3), (630, 3.3), (700, 3.3), (875, 4.1), (1050, 5.0)),
    6: ((960, 5.3), (1080, 5.3), (1200, 5.3), (1500, 6.6), (1800, 8.0)),
    7: ((1440, 8.5), (1620, 8.5), (1800, 8.5), (2250, 10.6), (2700, 12.8)),
}
_STANDARD_FUEL_MUL = (0.011, 0.010, 0.008, 0.010, 0.012)  # E, D, C, B, A

# Supercruise Overcharge (SCO) drives: size -> (opt_mass, max_fuel) per rating E, D, C, B, A.
_OVERCHARGE: dict[int, tuple[tuple[float, float], ...]] = {
    2: ((60, 0.6), (90, 0.9), (90, 0.9), (90, 0.9), (100, 1.0)),
    3: ((100, 1.2), (150, 1.8), (150, 1.8), (150, 1.8), (167, 1.9)),
    4: ((350, 2.0), (525, 3.0), (525, 3.0), (525, 3.0), (585, 3.2)),
    5: ((700, 3.3), (1050, 5.0), (1050, 5.0), (1050, 5.0), (1175, 5.2)),
    6: ((1200, 5.3), (1800, 8.0), (1800, 8.0), (1800, 8.0), (2000, 8.3)),
    7: ((1800, 8.5), (2700, 12.8), (2700, 12.8), (2700, 12.8), (3000, 13.1)),
    8: ((2800, 13.6), (4200, 20.4), (4200, 20.4), (4200, 20.4), (4670, 20.7)),
}
_OVERCHARGE_FUEL_MUL = (0.008, 0.012, 0.012, 0.012, 0.013)  # E, D, C, B, A

# Guardian FSD Booster: size -> flat range bonus (ly).
GUARDIAN_BOOST_LY = {1: 4.0, 2: 6.0, 3: 7.75, 4: 9.25, 5: 10.5}

# Neutron supercharge multiplies the range by 4; the SCO Mk II drive by 6 (as Spansh's own plotters
# treat it, 2026-10-12). Every other drive, SCO included, is x4.
_SUPERCHARGE_X6_SUFFIX = "_overchargebooster_mkii"


def supercharge_multiplier(item: str) -> int:
    return 6 if item.lower().endswith(_SUPERCHARGE_X6_SUFFIX) else 4


def _build() -> dict[tuple[bool, int, str], FsdSpec]:
    table: dict[tuple[bool, int, str], FsdSpec] = {}
    for overcharge, sizes, muls in (
        (False, _STANDARD, _STANDARD_FUEL_MUL),
        (True, _OVERCHARGE, _OVERCHARGE_FUEL_MUL),
    ):
        for size, rows in sizes.items():
            for rating, (opt_mass, max_fuel), fuel_mul in zip(RATINGS, rows, muls, strict=True):
                table[(overcharge, size, rating)] = FsdSpec(opt_mass, max_fuel, fuel_mul, _FUEL_POWER[size])
    return table


FSD_TABLE = _build()
