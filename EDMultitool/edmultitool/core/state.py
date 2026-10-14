"""Game state folded from core events. Immutable: `apply` returns a new GameState."""

from dataclasses import dataclass, field, replace

from edmultitool.catalog.ships import ship_display_name
from edmultitool.core.events import (
    Arrived,
    CargoChanged,
    CrewChanged,
    Event,
    FuelChanged,
    GameClosed,
    GameLoaded,
    LoadoutChanged,
    ShipNamed,
    StarPos,
)
from edmultitool.core.fleet import Fleet
from edmultitool.core.jumprange import best_case_range, jump_range


@dataclass(frozen=True, slots=True)
class GameState:
    system: str | None = None
    address: int | None = None
    star_pos: StarPos | None = None
    ship: LoadoutChanged | None = None
    fuel_main: float | None = None
    fuel_reservoir: float | None = None
    cargo: int = 0
    in_other_ship: bool = False
    closed: bool = False  # the game was quit (Shutdown); cleared by the next thing it says
    fuel_ship_id: int | None = None  # ship the fuel level was reported for (LoadGame precedes Loadout)
    ship_names: dict[str, str] = field(default_factory=dict)  # learned symbol → localised name; never mutated

    @property
    def ship_display(self) -> str | None:
        if self.ship is None:
            return None
        return ship_display_name(self.ship.ship_type, self.ship_names)

    @property
    def position_known(self) -> bool:
        return not self.in_other_ship and self.star_pos is not None

    def current_range(self) -> float | None:
        """Laden range right now (cargo + current fuel). None if ship/fuel unknown."""
        if self.ship is None or self.ship.fsd is None or self.in_other_ship:
            return None
        fuel = self.fuel_main if self.fuel_main is not None else self.ship.fuel_capacity_main
        reservoir = (
            self.fuel_reservoir if self.fuel_reservoir is not None else self.ship.fuel_capacity_reserve
        )
        return jump_range(
            self.ship.fsd,
            unladen_mass=self.ship.unladen_mass,
            cargo=self.cargo,
            fuel_main=fuel,
            fuel_reservoir=reservoir,
            guardian_boost_ly=self.ship.guardian_boost_ly,
        )

    def full_tank_range(self) -> float | None:
        """Laden range with current cargo and full tanks — the conservative figure for routing."""
        if self.ship is None or self.ship.fsd is None or self.in_other_ship:
            return None
        return jump_range(
            self.ship.fsd,
            unladen_mass=self.ship.unladen_mass,
            cargo=self.cargo,
            fuel_main=self.ship.fuel_capacity_main,
            fuel_reservoir=self.ship.fuel_capacity_reserve,
            guardian_boost_ly=self.ship.guardian_boost_ly,
        )

    def best_case_range(self) -> float | None:
        """Equivalent of the game's MaxJumpRange (empty hold, one jump of fuel)."""
        if self.ship is None or self.ship.fsd is None:
            return None
        return best_case_range(
            self.ship.fsd, unladen_mass=self.ship.unladen_mass, guardian_boost_ly=self.ship.guardian_boost_ly
        )


def apply(state: GameState, event: Event) -> GameState:
    if isinstance(event, GameClosed):
        return replace(state, closed=True)
    if state.closed:
        state = replace(state, closed=False)
    match event:
        case Arrived():
            if event.multicrew:
                return replace(state, in_other_ship=True, system=None, address=None, star_pos=None)
            fuel = event.fuel_level if event.fuel_level is not None else state.fuel_main
            if fuel is not None and state.ship is not None:  # never more than the tank holds
                fuel = min(fuel, state.ship.fuel_capacity_main)
            return replace(
                state,
                system=event.system,
                address=event.address,
                star_pos=event.star_pos,
                fuel_main=fuel,
                in_other_ship=False,
            )
        case LoadoutChanged():
            if state.in_other_ship:
                return state  # crew in someone else's ship: a loadout then is the host's, never ours
            if state.ship is not None and event.ship_id != state.ship.ship_id:
                # Different ship: the old ship's cargo no longer applies (next Cargo event sets it), and
                # its fuel only if LoadGame already reported fuel for THIS ship (login order: LoadGame,
                # Location, Loadout). Unknown fuel → full-tank assumption until Status/FuelScoop.
                keep_fuel = event.ship_id is not None and state.fuel_ship_id == event.ship_id
                return replace(
                    state,
                    ship=event,
                    fuel_main=min(state.fuel_main, event.fuel_capacity_main)
                    if keep_fuel and state.fuel_main is not None
                    else None,
                    fuel_reservoir=state.fuel_reservoir if keep_fuel else None,
                    cargo=0,
                )
            fuel = state.fuel_main
            if fuel is not None:
                fuel = min(fuel, event.fuel_capacity_main)
            return replace(state, ship=event, fuel_main=fuel)
        case FuelChanged():
            if state.in_other_ship:
                return state  # the host's fuel
            main = state.fuel_main
            same_ship = state.ship is not None and event.ship_id in (None, state.ship.ship_id)
            if event.main is not None:
                main = event.main
                if same_ship and state.ship is not None:  # never more than the tank holds
                    main = min(main, state.ship.fuel_capacity_main)
            elif event.added is not None and main is not None:
                cap = state.ship.fuel_capacity_main if state.ship else None
                main = main + event.added if cap is None else min(cap, main + event.added)
            reservoir = event.reservoir if event.reservoir is not None else state.fuel_reservoir
            owner = (
                event.ship_id if event.ship_id is not None else (state.ship.ship_id if state.ship else None)
            )
            return replace(state, fuel_main=main, fuel_reservoir=reservoir, fuel_ship_id=owner)
        case ShipNamed():
            return replace(state, ship_names={**state.ship_names, event.symbol: event.name})
        case GameLoaded():
            if event.ship is not None:
                state = apply(state, event.ship)
            return apply(state, event.fuel) if event.fuel is not None else state
        case CargoChanged():
            return replace(state, cargo=event.count)
        case CrewChanged():
            if event.in_other_ship:
                return replace(state, in_other_ship=True, system=None, address=None, star_pos=None)
            return replace(state, in_other_ship=False)
    return state


def plan_state(state: GameState, fleet: Fleet, pick: int | None) -> GameState:
    """The state routes are planned from. `pick` = a remembered ship chosen on the page (None = the ship
    you sit in): its build with full tanks and an empty hold, at your current position. Picking the ship
    you sit in is the same as no pick (its real cargo and fuel count). Unknown pick → `state` itself."""
    stored = fleet.get(pick)
    if stored is None or (state.ship is not None and state.ship.ship_id == pick and not state.in_other_ship):
        return state
    return replace(
        state, ship=stored.loadout, cargo=0, fuel_main=None, fuel_reservoir=None, in_other_ship=False
    )
