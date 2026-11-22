"""Loot memory and plan: remember crates and cubes, pick the densest nearby, scout unseen ground when empty-handed."""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional, Tuple

Vec = Tuple[float, float]
CELL = 40.0
SEE_R = 40.0  # inside near_range everything with line of sight is listed, so a missing item there is gone
VALUE = {"cubes": 1.0, "crates": 0.9}


class Loot:
    def __init__(self, nav: Any) -> None:
        self.nav = nav
        self.mem: Dict[str, Dict[int, List[float]]] = {"cubes": {}, "crates": {}}
        self.visited: set = set()
        self.goal: Optional[Tuple[str, int]] = None
        self.scout: Optional[Vec] = None

    def update(self, obs: Dict[str, Any], here: Vec, eye: List[float]) -> None:
        for kind, items in self.mem.items():
            seen = {c["id"]: c["pos"] for c in obs.get(kind, [])}
            items.update(seen)
            for i, p in list(items.items()):
                if i not in seen and math.hypot(p[0] - here[0], p[1] - here[1]) < SEE_R \
                        and (self.nav is None or self.nav.sees(eye, [p[0], p[1], p[2] + 1.2])):
                    del items[i]
        cx, cy = int(here[0] // CELL), int(here[1] // CELL)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if math.hypot((cx + dx + 0.5) * CELL - here[0], (cy + dy + 0.5) * CELL - here[1]) < 45:
                    self.visited.add((cx + dx, cy + dy))

    def forget(self, ident: Any) -> None:
        for items in self.mem.values():
            items.pop(ident, None)

    def pick(self, here: Vec, need: bool, ok: Callable[[float, float], bool], bad: set, toward: Vec = (0.0, 0.0)) -> Optional[Vec]:
        """Best remembered crate or cube by value-with-neighbours over distance; scout when needy and nothing is known."""
        cand = [(k, i, p) for k, items in self.mem.items() for i, p in items.items() if i not in bad and ok(p[0], p[1])]
        reach = 260.0 if need else 120.0
        best: Optional[Tuple[float, Tuple[str, int], List[float]]] = None
        for k, i, p in cand:
            d = math.hypot(p[0] - here[0], p[1] - here[1])
            if d > reach:
                continue
            dens = sum(VALUE[k2] for k2, i2, p2 in cand if i2 != i and math.hypot(p2[0] - p[0], p2[1] - p[1]) < 35)
            s = (VALUE[k] + 0.5 * dens) / (d + 25.0) * (1.25 if self.goal == (k, i) else 1.0)
            if best is None or s > best[0]:
                best = (s, (k, i), p)
        if best:
            self.goal, self.scout = best[1], None
            return (best[2][0], best[2][1])
        self.goal = None
        if not need:
            return None
        if self.scout and (math.hypot(self.scout[0] - here[0], self.scout[1] - here[1]) < 12
                           or (int(self.scout[0] // CELL), int(self.scout[1] // CELL)) in self.visited):
            self.scout = None
        if self.scout is None:
            cells = [(cx, cy) for cx in range(int(here[0] // CELL) - 6, int(here[0] // CELL) + 7)
                     for cy in range(int(here[1] // CELL) - 6, int(here[1] // CELL) + 7) if (cx, cy) not in self.visited]
            pts = [((cx + 0.5) * CELL, (cy + 0.5) * CELL) for cx, cy in cells]
            pts = [q for q in pts if ok(q[0], q[1]) and (self.nav is None or self.nav.free(q[0], q[1]))]
            if pts:
                self.scout = min(pts, key=lambda q: math.hypot(q[0] - here[0], q[1] - here[1]) + 0.7 * math.hypot(q[0] - toward[0], q[1] - toward[1]))
        return self.scout
