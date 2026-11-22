"""Last stand: with several hunters already on us, running only trades damage for nothing, so keep fighting."""
from typing import Any, List


def committed(mechs: List[Any], hpf: float, near: float = 110.0, need: int = 2) -> bool:
    """True when we are hurt and at least `need` mechs are within `near` metres: no retreat can break contact."""
    return hpf < 0.4 and sum(1 for f in mechs if f[0] < near) >= need
