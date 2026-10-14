"""Exobiology genus names. The journal and Spansh's body search name a genus by its codex key
("$Codex_Ent_Shrubs_Genus_Name;" / "Shrubs"); players know it by the name the game shows ("Frutexa"),
which is also the genus Spansh's species sightings carry."""

GENUS_NAMES: dict[str, str] = {
    "Aleoids": "Aleoida",
    "Bacterial": "Bacterium",
    "Cactoid": "Cactoida",
    "Cactoids": "Cactoida",
    "Clypeus": "Clypeus",
    "Conchas": "Concha",
    "Electricae": "Electricae",
    "Fonticulus": "Fonticulua",
    "Shrubs": "Frutexa",
    "Fumerolas": "Fumerola",
    "Fungoids": "Fungoida",
    "Osseus": "Osseus",
    "Recepta": "Recepta",
    "Stratum": "Stratum",
    "Tubus": "Tubus",
    "Tussocks": "Tussock",
    "Ground_Struct_Ice": "Crystalline Shards",
    "Brancae": "Brain Tree",
    "Sphere": "Anemone",
    "Cone": "Bark Mound",
    "Vents": "Amphora Plant",
    "Tube": "Sinuous Tubers",
}


def genus_name(key: str) -> str:
    """The shown name of a genus from its codex key, with or without the "$Codex_Ent_…_Genus_Name;"
    wrapping; an unknown key is shown as it came."""
    k = key.strip().removeprefix("$Codex_Ent_").removesuffix(";").removesuffix("_Genus_Name")
    return GENUS_NAMES.get(k, k)
