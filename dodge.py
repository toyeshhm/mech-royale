"""Sidestep incoming bullets and charging laser lines."""
import math
from typing import Any, Dict, List, Tuple

DODGE_T, DODGE_R, LASER_R = 0.6, 3.6, 5.0


def dodge_vec(c: List[float], projs: List[Dict[str, Any]], mechs: List[Any], sign: float) -> Tuple[float, float]:
    """Sidestep away from bullets whose line passes within reach of us, and out of the line of a charging laser."""
    ox = oy = 0.0
    for p in projs:
        if p.get("kind") not in ("gun", "npc"):
            continue
        v = p["vel"]
        vv = v[0] * v[0] + v[1] * v[1] + v[2] * v[2]
        if vv < 1:
            continue
        r = [p["pos"][i] - c[i] for i in range(3)]
        t = -(r[0] * v[0] + r[1] * v[1] + r[2] * v[2]) / vv
        if not 0.0 < t < DODGE_T:
            continue
        o = [r[i] + v[i] * t for i in range(3)]  # bullet minus us at closest approach
        if math.hypot(o[0], o[1], o[2]) > DODGE_R:
            continue
        h = math.hypot(o[0], o[1])
        if h > 0.3:
            dx, dy = -o[0] / h, -o[1] / h
        else:
            vh = math.hypot(v[0], v[1]) or 1.0
            n = (v[0] / vh, v[1] / vh)
            dx, dy = -n[1] * sign, n[0] * sign
        w = 1.0 / (t + 0.1)
        ox += dx * w
        oy += dy * w
    for f in mechs:
        m = f[1]
        if not m.get("charging") or f[0] > 280:
            continue
        a = m["aim"]
        ux, uy = math.cos(a[0]), math.sin(a[0])
        rx, ry = c[0] - m["pos"][0], c[1] - m["pos"][1]
        along = rx * ux + ry * uy
        side = rx * -uy + ry * ux
        if along > 0 and abs(side) < LASER_R:
            s = 1.0 if side >= 0 else -1.0
            ox += -uy * s * 2.0
            oy += ux * s * 2.0
    return (ox, oy)
