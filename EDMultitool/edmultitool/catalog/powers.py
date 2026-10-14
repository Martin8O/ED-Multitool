"""The 12 Powerplay powers and the system power states, spelled as the journal and Spansh spell them
(Spansh's `/systems/field_values/controlling_power` and `/power_state`, checked 2026-10-12)."""

POWERS: tuple[str, ...] = (
    "A. Lavigny-Duval",
    "Aisling Duval",
    "Archon Delaine",
    "Denton Patreus",
    "Edmund Mahon",
    "Felicia Winters",
    "Jerome Archer",
    "Li Yong-Rui",
    "Nakato Kaine",
    "Pranav Antal",
    "Yuri Grom",
    "Zemina Torval",
)

# A controlled system is Exploited, Fortified or Stronghold; any other system is Unoccupied (powers may
# be present there, acquiring it).
CONTROLLED_STATES: tuple[str, ...] = ("Exploited", "Fortified", "Stronghold")
POWER_STATES: tuple[str, ...] = (*CONTROLLED_STATES, "Unoccupied")
