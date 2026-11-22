"""Keep a clear line to the target while holding a laser: step to the nearest spot that sees it."""
import math
from typing import Optional, Sequence, Tuple

Vec = Tuple[float, float]
RINGS = (8.0, 16.0, 26.0)
N_DIR = 12


class ClearLine:
    def __init__(self) -> None:
        self.goal: Optional[Vec] = None
        self.at = -99.0

    def step(self, nav: object, here: Vec, z: float, mh: float, tpos: Sequence[float], tick: float,
             lo: float = 25.0, hi: float = 240.0) -> Optional[Vec]:
        """A free point within ~26 m that sees the target at lo..hi metres, nearest first. Cached for 3 ticks."""
        if self.goal is not None and tick - self.at < 3:
            return self.goal
        self.at = tick
        self.goal = None
        px, py = here
        best = 1e9
        for r in RINGS:
            for k in range(N_DIR):
                a = k * 2 * math.pi / N_DIR
                x, y = px + r * math.cos(a), py + r * math.sin(a)
                d = math.hypot(tpos[0] - x, tpos[1] - y)
                if not lo <= d <= hi or not nav.free(x, y):  # type: ignore[attr-defined]
                    continue
                cost = r + abs(d - 110.0) * 0.1
                if cost < best and nav.sees([x, y, nav.ground(x, y) + mh], tpos):  # type: ignore[attr-defined]
                    best, self.goal = cost, (x, y)
            if self.goal is not None:
                break
        return self.goal
