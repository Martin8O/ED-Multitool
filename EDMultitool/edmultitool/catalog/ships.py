"""Ship symbol → display name (game facts).

The journal's Loadout only has the lower-case symbol; LoadGame / Shipyard* events carry the localised
name, which `core.state` learns at runtime and prefers over this table. Unknown symbols fall back to
a tidied symbol, so a new ship never breaks the panel.
"""

SHIP_NAMES: dict[str, str] = {
    "adder": "Adder",
    "anaconda": "Anaconda",
    "asp": "Asp Explorer",
    "asp_scout": "Asp Scout",
    "belugaliner": "Beluga Liner",
    "cobramkiii": "Cobra Mk III",
    "cobramkiv": "Cobra Mk IV",
    "cobramkv": "Cobra Mk V",
    "corsair": "Corsair",
    "cutter": "Imperial Cutter",
    "diamondback": "Diamondback Scout",
    "diamondbackxl": "Diamondback Explorer",
    "dolphin": "Dolphin",
    "eagle": "Eagle",
    "empire_courier": "Imperial Courier",
    "empire_eagle": "Imperial Eagle",
    "empire_trader": "Imperial Clipper",
    "federation_corvette": "Federal Corvette",
    "federation_dropship": "Federal Dropship",
    "federation_dropship_mkii": "Federal Assault Ship",
    "federation_gunship": "Federal Gunship",
    "ferdelance": "Fer-de-Lance",
    "hauler": "Hauler",
    "independant_trader": "Keelback",
    "krait_light": "Krait Phantom",
    "krait_mkii": "Krait Mk II",
    "mamba": "Mamba",
    "mandalay": "Mandalay",
    "mediumtransport01": "Lynx Highliner",
    "orca": "Orca",
    "panthermkii": "Panther Clipper Mk II",
    "python": "Python",
    "python_nx": "Python Mk II",
    "sidewinder": "Sidewinder",
    "type6": "Type-6 Transporter",
    "type7": "Type-7 Transporter",
    "type8": "Type-8 Transporter",
    "type9": "Type-9 Heavy",
    "type9_military": "Type-10 Defender",
    "typex": "Alliance Chieftain",
    "typex_2": "Alliance Crusader",
    "typex_3": "Alliance Challenger",
    "viper": "Viper Mk III",
    "viper_mkiv": "Viper Mk IV",
    "vulture": "Vulture",
}


# Landing pad each ship needs (game facts). A ship not listed is treated as needing a large pad.
SHIP_PADS: dict[str, str] = {
    **dict.fromkeys(
        (
            "adder", "cobramkiii", "cobramkiv", "cobramkv", "diamondback", "dolphin", "eagle",
            "empire_courier", "empire_eagle", "hauler", "sidewinder", "viper", "viper_mkiv", "vulture",
        ),
        "S",
    ),
    **dict.fromkeys(
        (
            "asp", "asp_scout", "corsair", "diamondbackxl", "federation_dropship", "federation_dropship_mkii",
            "federation_gunship", "ferdelance", "independant_trader", "krait_light", "krait_mkii", "mamba",
            "mandalay", "mediumtransport01", "python", "python_nx", "type6", "type8", "typex", "typex_2",
            "typex_3",
        ),
        "M",
    ),
    **dict.fromkeys(
        (
            "anaconda", "belugaliner", "cutter", "empire_trader", "federation_corvette", "orca",
            "panthermkii", "type7", "type9", "type9_military",
        ),
        "L",
    ),
}  # fmt: skip


def ship_pad(symbol: str) -> str:
    return SHIP_PADS.get(symbol.lower(), "L")


def ship_display_name(symbol: str, learned: dict[str, str] | None = None) -> str:
    key = symbol.lower()
    if learned and key in learned:
        return learned[key]
    if key in SHIP_NAMES:
        return SHIP_NAMES[key]
    return key.replace("_", " ").title()
