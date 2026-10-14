"""On-foot gear (game facts): the suits and weapons Pioneer Supplies sells, their modifications, and the
spellings players use for them in forum posts.

Every pattern is a regular expression matched on lower-case text between word boundaries. Spellings come
from real posts of the forum's gear-sharing thread (tests/data/gear_posts.txt), typos included.
"""

from typing import Literal

GearKind = Literal["suit", "weapon"]

# canonical name → (kind, class shown to the player, spellings)
ITEMS: dict[str, tuple[GearKind, str, tuple[str, ...]]] = {
    "Maverick": ("suit", "suit", (r"maverick", r"maverik", r"mav")),
    "Dominator": ("suit", "suit", (r"dominator", r"dominador", r"domenator", r"dom")),
    "Artemis": ("suit", "suit", (r"artemis", r"artimes", r"artimis", r"art")),
    "Karma AR-50": (
        "weapon",
        "kinetic assault rifle",
        (r"(?:karma\s*)?ar-?\s?50(?!-\d)", r"kinetic (?:assault )?rifle"),
    ),
    "Karma C-44": ("weapon", "kinetic SMG", (r"(?:karma\s*)?c-?\s?44(?!-\d)", r"kinetic smg")),
    "Karma L-6": ("weapon", "kinetic rocket launcher", (r"(?:karma\s*)?l-?\s?6(?!-\d)", r"rocket launcher")),
    "Karma P-15": ("weapon", "kinetic pistol", (r"(?:karma\s*)?p-?\s?15(?!-\d)", r"kinetic pistol")),
    "TK Aphelion": (
        "weapon",
        "laser rifle",
        (
            r"(?:tk |takada )?(?:aphelion|apheleion|apelion|aphelium)",
            r"(?:tk |takada )?laser rifle",
            r"tk rifle",
        ),
    ),
    "TK Eclipse": ("weapon", "laser SMG", (r"(?:tk |takada )?eclipse", r"laser smg")),
    "TK Zenith": ("weapon", "laser pistol", (r"(?:tk |takada )?(?:zenith|zentih)s?", r"laser pistol")),
    "Manticore Executioner": ("weapon", "plasma sniper rifle", (r"(?:manticore |maticore )?executioner",)),
    "Manticore Intimidator": ("weapon", "plasma shotgun", (r"(?:manticore |maticore )?intimidator",)),
    "Manticore Oppressor": ("weapon", "plasma assault rifle", (r"(?:manticore |maticore )?oppressor",)),
    "Manticore Tormentor": (
        "weapon",
        "plasma pistol",
        (r"(?:manticore |maticore )?(?:tormentor|tourmentor)",),
    ),
}

# canonical name → (fits, spellings); fits = "suit" or "weapon"
MODS: dict[str, tuple[GearKind, tuple[str, ...]]] = {
    "Added Melee Damage": ("suit", (r"(?:added |increased |inc\.? )?melee(?: damage)?",)),
    "Combat Movement Speed": ("suit", (r"combat (?:movement )?speed",)),
    "Damage Resistance": ("suit", (r"(?:dmg|damage) resist(?:ance)?",)),
    "Enhanced Tracking": ("suit", (r"enhanced tracking",)),
    "Extra Ammo Capacity": ("suit", (r"(?:extra |increased |inc\.? )ammo(?: capacity| cap\.?)?",)),
    "Extra Backpack Capacity": ("suit", (r"(?:extra )?backpack(?: capacity| cap\.?)?",)),
    "Faster Shield Regen": ("suit", (r"(?:faster )?shield regen(?:eration)?",)),
    "Improved Battery Capacity": (
        "suit",
        (r"(?:improved |increased |inc\.? )?(?:battery|batt\.?) cap(?:acity|\.)?",),
    ),
    "Improved Jump Assist": ("suit", (r"(?:improved )?jump assist", r"jetpack")),
    "Increased Air Reserves": ("suit", (r"(?:increased |extra )?(?:air reserves?|o2|oxygen)",)),
    "Increased Sprint Duration": ("suit", (r"(?:increased |inc\.? |extra |extended )?sprint(?: duration)?",)),
    "Night Vision": ("suit", (r"night ?vision", r"nv")),
    "Quieter Footsteps": ("suit", (r"quiet(?:er)? footsteps",)),
    "Reduced Tool Battery Consumption": (
        "suit",
        (r"(?:reduced )?tool (?:battery|batt\.?)(?: consumption)?",),
    ),
    "Audio Masking": ("weapon", (r"audio ?mask(?:ing)?",)),
    "Faster Handling": ("weapon", (r"fast(?:er)? handling",)),
    "Greater Range": ("weapon", (r"(?:greater|increased) range",)),
    "Headshot Damage": ("weapon", (r"head ?shot(?: damage)?",)),
    "Improved Hip Fire Accuracy": (
        "weapon",
        (r"(?:improved |higher |increased )?(?:accuracy|hip[ -]?fire(?: accuracy)?)",),
    ),
    "Magazine Size": ("weapon", (r"(?:increased |larger |extra )?(?:magazine|mag)(?: size| capacity)?",)),
    "Noise Suppressor": ("weapon", (r"noise supp?ress?or", r"suppressor", r"silencer")),
    "Reload Speed": ("weapon", (r"reload speed", r"faster reload(?:ing)?")),
    "Scope": ("weapon", (r"scope[d]?",)),
    "Stability": ("weapon", (r"stability", r"recoil")),
    "Stowed Reloading": ("weapon", (r"(?:stowed|holster) reload(?:ing)?",)),
}

# Modification slots by grade: grade 1 has none, each grade up adds one.
SLOTS_BY_GRADE = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4}

# --- rating: players' opinions (community guides 2022-23, Fandom wiki) -------
Quality = Literal["top", "good", "weak", "dead"]
QUALITY_POINTS: dict[str, float] = {"top": 1.0, "good": 0.5, "empty": 0.25, "weak": 0.0, "dead": -0.5}
GRADE_POINTS = {1: 0.0, 2: 1.0, 3: 2.0, 4: 3.0, 5: 4.0}
RARE_BONUS = 0.25  # a good or top mod only two engineers offer (hard to add yourself)
RARE_MODS = frozenset({"Night Vision", "Quieter Footsteps", "Audio Masking", "Headshot Damage"})
SUIT_BONUS = 0.5
WEAPON_TIER: dict[str, str] = {
    "Karma L-6": "A", "Manticore Tormentor": "A", "Manticore Executioner": "A", "Manticore Intimidator": "A",
    "TK Aphelion": "B", "Karma AR-50": "B", "Karma P-15": "B", "TK Zenith": "B",
    "TK Eclipse": "C", "Karma C-44": "C",
    "Manticore Oppressor": "D",
}  # fmt: skip
TIER_BONUS = {"A": 0.5, "B": 0.0, "C": -0.5, "D": -1.5}

# suit mod → quality on (Dominator, Maverick, Artemis)
SUIT_MOD_QUALITY: dict[str, tuple[Quality, Quality, Quality]] = {
    "Night Vision": ("top", "top", "good"),
    "Improved Battery Capacity": ("top", "top", "good"),
    "Damage Resistance": ("top", "good", "weak"),
    "Faster Shield Regen": ("top", "good", "weak"),
    "Improved Jump Assist": ("good", "good", "top"),
    "Extra Backpack Capacity": ("weak", "top", "weak"),
    "Reduced Tool Battery Consumption": ("dead", "good", "good"),
    "Extra Ammo Capacity": ("good", "weak", "dead"),
    "Combat Movement Speed": ("good", "weak", "weak"),
    "Quieter Footsteps": ("weak", "good", "dead"),
    "Increased Sprint Duration": ("weak", "weak", "good"),
    "Enhanced Tracking": ("weak", "weak", "dead"),
    "Increased Air Reserves": ("dead", "dead", "dead"),
    "Added Melee Damage": ("dead", "dead", "dead"),
}
SUIT_COLUMN = {"Dominator": 0, "Maverick": 1, "Artemis": 2}

# weapon mod → quality per weapon group; groups: L6 EXE INT TOR P15 ZEN AR50 APH SMG OPP
WEAPON_GROUP = {
    "Karma L-6": 0, "Manticore Executioner": 1, "Manticore Intimidator": 2, "Manticore Tormentor": 3,
    "Karma P-15": 4, "TK Zenith": 5, "Karma AR-50": 6, "TK Aphelion": 7, "Karma C-44": 8, "TK Eclipse": 8,
    "Manticore Oppressor": 9,
}  # fmt: skip
_T: Quality = "top"
_G: Quality = "good"
_W: Quality = "weak"
_D: Quality = "dead"
WEAPON_MOD_QUALITY: dict[str, tuple[Quality, ...]] = {
    "Magazine Size": (_T, _T, _T, _G, _G, _T, _G, _G, _G, _W),
    "Stowed Reloading": (_T, _G, _G, _G, _G, _G, _G, _G, _G, _W),
    "Reload Speed": (_G, _G, _G, _G, _G, _G, _G, _G, _G, _W),
    "Faster Handling": (_G, _W, _G, _G, _G, _G, _G, _G, _G, _W),
    "Scope": (_W, _T, _T, _W, _W, _W, _G, _G, _W, _W),
    "Audio Masking": (_W, _T, _G, _T, _G, _W, _G, _W, _W, _W),
    "Noise Suppressor": (_W, _G, _G, _T, _G, _W, _G, _W, _W, _W),
    "Headshot Damage": (_D, _G, _W, _G, _G, _W, _G, _G, _W, _W),
    "Greater Range": (_W, _G, _W, _G, _G, _W, _G, _G, _W, _W),
    "Stability": (_D, _W, _W, _G, _T, _T, _G, _G, _G, _G),
    "Improved Hip Fire Accuracy": (_D, _D, _W, _G, _W, _W, _W, _W, _W, _G),
}

# --- who an item suits: the suit's own job, plus what its mods are for ---------------------------------
ROLES = ("combat", "salvage", "exobiology", "stealth")  # the order they are shown in
SUIT_ROLE = {"Dominator": "combat", "Maverick": "salvage", "Artemis": "exobiology"}
MOD_ROLES: dict[str, tuple[str, ...]] = {
    "Damage Resistance": ("combat",),
    "Faster Shield Regen": ("combat",),
    "Extra Ammo Capacity": ("combat",),
    "Combat Movement Speed": ("combat",),
    "Extra Backpack Capacity": ("salvage",),
    "Quieter Footsteps": ("stealth",),
    "Reduced Tool Battery Consumption": ("salvage", "exobiology"),  # Arc Cutter and Genetic Sampler
    "Improved Jump Assist": ("exobiology",),
    "Increased Sprint Duration": ("exobiology",),
    "Audio Masking": ("stealth",),
    "Noise Suppressor": ("stealth",),
}
