"""Score upgrade offers: damage first, then survival."""
from typing import Any, Dict, List

# Offer scoring by effect path: survival first. Unknown paths score 1, so new upgrades still rank.
DMG_FIRST = {"laser_damage": 30, "gun_damage": 29, "gun_rate": 28, "laser_charge": 20}
PATH_SCORE = {"mech.armor": 10, "mech.hp": 9, "mech.regen_per_s": 8, "mech.regen_delay": 6,
              "mech.walk_speed": 7, "mech.boost_speed": 2, "mech.energy": 5, "mech.energy_regen": 4,
              "mech.boost_drain": 3, "mech.jet_drain": 1, "mech.turn_rate": 5, "mech.pitch_rate": 1,
              "mech.near_range_bonus": 3, "mech.vision_bonus": 2, "mech.hearing": 1}
WEAPON_SCORE = 3.5  # a weapon upgrade for a weapon we hold


def pick_upgrade(offer: Dict[str, Any], defs: List[Dict[str, Any]], held: List[str]) -> int:
    by_id = {u["id"]: u for u in defs}
    best, best_i = -1.0, 0
    for i, ch in enumerate(offer["choices"]):
        s = float(DMG_FIRST.get(ch["id"], 0))
        for e in by_id.get(ch["id"], {}).get("effects", []):
            path = str(e.get("path", ""))
            if path.startswith("weapons."):
                s += WEAPON_SCORE if path.split(".")[1] in held else 0.5
            else:
                s += PATH_SCORE.get(path, 1)
        if s > best:
            best, best_i = s, i
    return best_i
