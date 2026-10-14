"""What you have already explored, as routes see it. Pure; the store is `edmultitool.explored`.

A body is known by its Spansh id64: the system's id64 with the journal's BodyID in the top 9 bits
(checked against recorded Spansh replies). "Done" means:
- mapped: you mapped the body with probes (an FSS scan alone leaves its mapping value open);
- analysed: you took the third sample of that species on that body.
Data lost to a death before it was sold is not done any more (the store applies that rule).
"""

from dataclasses import dataclass

from edmultitool.core.events import ExploreEvent

# (event, the name of the system you were in when it happened — "" when not known)
Located = tuple[ExploreEvent, str]


def body_id64(system_address: int, body_id: int) -> int:
    """Spansh's body id64 from the journal's SystemAddress + BodyID."""
    return system_address | (body_id << 55)


def species_key(name: str) -> str:
    """Species names are matched without letter case: the journal's "Species_Localised" (in the game's
    language — English names match Spansh) against Spansh's species name."""
    return " ".join(name.split()).casefold()


@dataclass(frozen=True, slots=True)
class Explored:
    mapped: frozenset[int] = frozenset()  # body id64
    analysed: frozenset[tuple[int, str]] = frozenset()  # (body id64, species_key)

    def is_mapped(self, body: int) -> bool:
        return body in self.mapped

    def is_analysed(self, body: int, species: str) -> bool:
        return (body, species_key(species)) in self.analysed


NOTHING = Explored()
