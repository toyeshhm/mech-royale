"""Stop-and-shoot rhythm: stand still while a laser burst is about to land and few foes can see us."""
import os
from typing import Any, Dict, List, Optional

MAX_FOES = int(os.environ.get("TEMPO_MAXFOES", "1"))
NEAR = float(os.environ.get("TEMPO_NEAR", "60"))
FAR = float(os.environ.get("TEMPO_FAR", "190"))
CD = float(os.environ.get("TEMPO_CD", "99"))


def hold(laser: Optional[Dict[str, Any]], target: Any, mechs: List[Any], clear: bool) -> bool:
    """True when we should plant our feet: laser charging or nearly ready, clear shot, at most MAX_FOES in view."""
    if laser is None or target is None or target[1]["kind"] != "mech" or not clear or len(mechs) > MAX_FOES:
        return False
    if not NEAR < target[0] < min(FAR, laser["stats"]["range"] * 0.95):
        return False
    return laser.get("charge", 0) > 0 or laser["cooldown"] <= CD
