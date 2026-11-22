"""Static map model built from the kit's world files: walkable grid, A*, line of sight, cover."""
from __future__ import annotations

import array
import heapq
import json
import math
import os
import sys
from typing import Dict, List, Optional, Sequence, Tuple

Vec = Tuple[float, float]
CELL = 4.0
HEAD_CLEARANCE = 9.2
STEP_UP = 1.2
MAX_SLOPE = math.tan(math.radians(50))
NEIGHBORS = ((1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
             (1, 1, 1.414), (1, -1, 1.414), (-1, 1, 1.414), (-1, -1, 1.414))


class Nav:
    """Grid of 4 m cells. `blocked` marks cells a mech cannot stand in, `top` the solid height for sight."""

    def __init__(self, map_dir: str) -> None:
        with open(os.path.join(map_dir, "v2", "world.json"), encoding="utf-8") as f:
            world = json.load(f)
        t = world["terrain"]
        self.t_min_x, self.t_min_y = float(t["minX"]), float(t["minY"])
        self.t_step, self.t_nx, self.t_ny = float(t["step"]), int(t["nx"]), int(t["ny"])
        self.terrain = array.array("h")
        with open(os.path.join(map_dir, "v2", str(t["file"])), "rb") as f:
            self.terrain.fromfile(f, self.t_nx * self.t_ny)
        if sys.byteorder == "big":
            self.terrain.byteswap()
        b = world["bounds"]
        self.min_x, self.min_y = float(b["minX"]), float(b["minY"])
        self.nx = int(math.ceil((float(b["maxX"]) - self.min_x) / CELL))
        self.ny = int(math.ceil((float(b["maxY"]) - self.min_y) / CELL))
        n = self.nx * self.ny
        self.h = array.array("f", [0.0]) * n
        for j in range(self.ny):
            y = self.min_y + (j + 0.5) * CELL
            for i in range(self.nx):
                self.h[j * self.nx + i] = self.ground(self.min_x + (i + 0.5) * CELL, y)
        self.blocked = bytearray(n)
        self.near = bytearray(n)
        self.top = array.array("f", [-1000.0]) * n
        for p in world["prisms"]:
            self._rasterize(p)
        for c in range(n):
            if self.blocked[c]:
                i, j = c % self.nx, c // self.nx
                for dx, dy, _ in NEIGHBORS:
                    k = self._index(i + dx, j + dy)
                    if k >= 0:
                        self.near[k] = 1

    def ground(self, x: float, y: float) -> float:
        fx = max(0.0, min(self.t_nx - 1.001, (x - self.t_min_x) / self.t_step))
        fy = max(0.0, min(self.t_ny - 1.001, (y - self.t_min_y) / self.t_step))
        i, j = int(fx), int(fy)
        ax, ay = fx - i, fy - j
        r = j * self.t_nx + i
        tr = self.terrain
        low = tr[r] * (1 - ax) + tr[r + 1] * ax
        high = tr[r + self.t_nx] * (1 - ax) + tr[r + self.t_nx + 1] * ax
        return (low * (1 - ay) + high * ay) * 0.01

    def _index(self, i: int, j: int) -> int:
        return j * self.nx + i if 0 <= i < self.nx and 0 <= j < self.ny else -1

    def cell(self, x: float, y: float) -> int:
        i = max(0, min(self.nx - 1, int((x - self.min_x) / CELL)))
        j = max(0, min(self.ny - 1, int((y - self.min_y) / CELL)))
        return j * self.nx + i

    def center(self, c: int) -> Vec:
        return (self.min_x + (c % self.nx + 0.5) * CELL, self.min_y + (c // self.nx + 0.5) * CELL)

    def _rasterize(self, prism: Dict[str, object]) -> None:
        """Even-odd scanline fill, so holes are subtracted without a separate test."""
        rings: List[Sequence[float]] = [prism["ring"]]  # type: ignore[list-item]
        rings.extend(prism.get("holes") or [])  # type: ignore[arg-type, attr-defined]
        edges: List[Tuple[float, float, float, float]] = []
        for flat in rings:
            pts = [(float(flat[k]), float(flat[k + 1])) for k in range(0, len(flat) - 1, 2)]
            edges.extend((a[0], a[1], b[0], b[1]) for a, b in zip(pts, pts[1:] + pts[:1]))
        if not edges:
            return
        base, top = float(prism["base"]), float(prism["top"])  # type: ignore[arg-type]
        bridge = prism.get("kind") == "bridge"  # type: ignore[attr-defined]
        y0 = min(min(e[1], e[3]) for e in edges)
        y1 = max(max(e[1], e[3]) for e in edges)
        y = math.floor(y0 / 2.0) * 2.0 + 1.0
        while y <= y1:
            xs = sorted(ax + (y - ay) * (bx - ax) / (by - ay) for ax, ay, bx, by in edges if (ay <= y) != (by <= y))
            for a, b in zip(xs[::2], xs[1::2]):
                self._mark(a, b, y, base, top, bridge)
            y += 2.0

    def _mark(self, a: float, b: float, y: float, base: float, top: float, bridge: bool) -> None:
        j = int((y - self.min_y) / CELL)
        if not 0 <= j < self.ny:
            return
        lo = max(0, int((a - self.min_x) / CELL))
        hi = min(self.nx - 1, int((b - self.min_x) / CELL))
        for i in range(lo, hi + 1):
            c = j * self.nx + i
            g = self.h[c]
            if not bridge:
                if top > self.top[c]:
                    self.top[c] = top
                if base <= g + HEAD_CLEARANCE and top > g + STEP_UP:
                    self.blocked[c] = 1

    def free(self, x: float, y: float) -> bool:
        return not self.blocked[self.cell(x, y)]

    def nearest_free(self, c: int, reach: int = 6) -> int:
        if not self.blocked[c]:
            return c
        i0, j0 = c % self.nx, c // self.nx
        for r in range(1, reach + 1):
            for dj in range(-r, r + 1):
                for di in (-r, r) if abs(dj) != r else range(-r, r + 1):
                    k = self._index(i0 + di, j0 + dj)
                    if k >= 0 and not self.blocked[k]:
                        return k
        return c

    def sees(self, a: Sequence[float], b: Sequence[float]) -> bool:
        """Line of sight between two 3D points against building columns."""
        d = math.dist(a, b)
        steps = max(1, int(d / 2.0))
        for s in range(1, steps):
            f = s / steps
            z = a[2] + (b[2] - a[2]) * f
            if self.top[self.cell(a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)] > z:
                return False
        return True

    def clear_walk(self, a: Vec, b: Vec) -> bool:
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        steps = max(1, int(d / 2.0))
        last = self.h[self.cell(a[0], a[1])]
        for s in range(1, steps + 1):
            f = s / steps
            c = self.cell(a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
            if self.blocked[c] or abs(self.h[c] - last) > MAX_SLOPE * 2.8:
                return False
            last = self.h[c]
        return True

    def path(self, start: Vec, goal: Vec, budget: int = 3000) -> List[Vec]:
        """Weighted A*. On budget exhaustion it returns the route to the node closest to the goal."""
        s = self.nearest_free(self.cell(start[0], start[1]))
        g = self.nearest_free(self.cell(goal[0], goal[1]))
        if s == g:
            return [goal]
        nx, ny, blocked, near, h = self.nx, self.ny, self.blocked, self.near, self.h
        gi, gj = g % nx, g // nx

        def est(c: int) -> float:
            dx, dy = abs(c % nx - gi), abs(c // nx - gj)
            return (dx + dy - 0.586 * min(dx, dy)) * 1.35

        best: Dict[int, float] = {s: 0.0}
        parent: Dict[int, int] = {}
        heap: List[Tuple[float, int]] = [(est(s), s)]
        closest, closest_h = s, est(s)
        done = -1
        for _ in range(budget):
            if not heap:
                break
            _, c = heapq.heappop(heap)
            if c == g:
                done = c
                break
            base = best[c]
            i, j = c % nx, c // nx
            for dx, dy, w in NEIGHBORS:
                x, y = i + dx, j + dy
                if not (0 <= x < nx and 0 <= y < ny):
                    continue
                k = y * nx + x
                if blocked[k] or abs(h[k] - h[c]) > MAX_SLOPE * CELL * w:
                    continue
                if dx and dy and (blocked[j * nx + x] or blocked[y * nx + i]):
                    continue
                cost = base + w * (1.6 if near[k] else 1.0)
                if cost < best.get(k, 1e18):
                    best[k], parent[k] = cost, c
                    e = est(k)
                    if e < closest_h:
                        closest, closest_h = k, e
                    heapq.heappush(heap, (cost + e, k))
        end = done if done >= 0 else closest
        cells = [end]
        while cells[-1] != s:
            cells.append(parent[cells[-1]])
        cells.reverse()
        pts = [self.center(c) for c in cells[1:]]
        if done >= 0:
            pts.append(goal if self.free(goal[0], goal[1]) else self.center(g))
        return pts or [goal]

    def cover_from(self, me: Sequence[float], foes: Sequence[Sequence[float]], reach: float = 44.0) -> Optional[Vec]:
        """Nearest walkable spot, reachable in a straight line, hidden from every listed foe."""
        best: Optional[Vec] = None
        best_cost = 1e18
        for r in (10.0, 18.0, 28.0, reach):
            for a in range(16):
                th = a * math.pi / 8
                x, y = me[0] + r * math.cos(th), me[1] + r * math.sin(th)
                if not self.free(x, y) or not self.clear_walk((me[0], me[1]), (x, y)):
                    continue
                eye = (x, y, self.h[self.cell(x, y)] + 6.0)
                if any(self.sees(eye, (f[0], f[1], f[2] + 7.0)) for f in foes):
                    continue
                away = min(math.hypot(x - f[0], y - f[1]) for f in foes) if foes else 0.0
                cost = r - 0.25 * away
                if cost < best_cost:
                    best, best_cost = (x, y), cost
            if best is not None:
                return best
        return None


class Walker:
    """Follows a cached A* route and replans when the goal moves or we leave the route."""

    def __init__(self, nav: Nav) -> None:
        self.nav = nav
        self.route: List[Vec] = []
        self.goal: Optional[Vec] = None
        self.age = 0

    def step(self, pos: Vec, goal: Vec) -> Vec:
        """Unit direction for this decision."""
        self.age += 1
        moved = self.goal is None or math.hypot(goal[0] - self.goal[0], goal[1] - self.goal[1]) > 8.0
        if moved or not self.route or self.age > 25:
            self.route = self.nav.path(pos, goal)
            self.goal, self.age = goal, 0
        while len(self.route) > 1 and math.hypot(self.route[0][0] - pos[0], self.route[0][1] - pos[1]) < 3.5:
            self.route.pop(0)
        target = self.route[0]
        for p in self.route[1:9]:
            if not self.nav.clear_walk(pos, p):
                break
            target = p
        dx, dy = target[0] - pos[0], target[1] - pos[1]
        n = math.hypot(dx, dy)
        return (dx / n, dy / n) if n > 1e-6 else (0.0, 0.0)

    def reset(self) -> None:
        self.route, self.goal, self.age = [], None, 0
