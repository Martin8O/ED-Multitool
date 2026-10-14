"""Mission kinds in plain words, keyed by the first part of the game's internal mission name
("Mission_PassengerBulk_BUSINESS_ARRIVING" → "PassengerBulk"). Our own wording; an unknown kind is spelled
out from its name."""

import re

MISSION_KINDS: dict[str, str] = {
    "Altruism": "Donate goods",
    "AltruismCredits": "Donate credits",
    "Assassinate": "Assassination",
    "Collect": "Source goods",
    "Courier": "Courier",
    "Delivery": "Delivery",
    "Disable": "Disable a ship",
    "Hack": "Hack",
    "HackMegaship": "Hack a megaship",
    "LongDistanceExpedition": "Expedition",
    "Massacre": "Massacre",
    "MassacreWing": "Massacre (wing)",
    "Mining": "Mining",
    "OnFoot": "On foot",
    "PassengerBulk": "Passengers",
    "PassengerVIP": "VIP passenger",
    "Piracy": "Piracy",
    "Rescue": "Rescue",
    "Salvage": "Salvage",
    "Scan": "Scan",
    "Sightseeing": "Sightseeing",
    "Smuggle": "Smuggling",
}

_WORDS = re.compile(r"[A-Z][a-z]+|[A-Z]+(?![a-z])|[a-z]+|\d+")


def mission_kind(code: str) -> str:
    """ "Mission_PassengerBulk_BUSINESS_ARRIVING" → "Passengers"; "" when there is no name at all."""
    parts = [p for p in code.removeprefix("Mission_").removesuffix("_name").split("_") if p]
    if not parts:
        return ""
    if parts[0] in MISSION_KINDS:
        return MISSION_KINDS[parts[0]]
    words = _WORDS.findall(parts[0])
    return " ".join(words).capitalize() if words else parts[0]
