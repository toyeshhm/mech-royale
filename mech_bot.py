"""Safe Mech Royale bot (mechfight/1): survive the zone first, shoot what is in front of us."""
from __future__ import annotations

import math
import random
import traceback
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

import mechfight as mf

Vec = Tuple[float, float]
TAU = 2 * math.pi
# Offer scoring by effect path: survival first. Unknown paths score 1, so new upgrades still rank.
PATH_SCORE = {"mech.armor": 10, "mech.hp": 9, "mech.regen_per_s": 8, "mech.regen_delay": 6,
              "mech.walk_speed": 7, "mech.boost_speed": 2, "mech.energy": 5, "mech.energy_regen": 4,
              "mech.boost_drain": 3, "mech.jet_drain": 1, "mech.turn_rate": 5, "mech.pitch_rate": 1,
              "mech.near_range_bonus": 3, "mech.vision_bonus": 2, "mech.hearing": 1}
WEAPON_SCORE = 3.5  # a weapon upgrade for a weapon we hold


def wrap(a: float) -> float:
    return (a + math.pi) % TAU - math.pi


def unit(dx: float, dy: float) -> Vec:
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n > 1e-9 else (0.0, 0.0)


def rotate(v: Vec, a: float) -> Vec:
    c, s = math.cos(a), math.sin(a)
    return (v[0] * c - v[1] * s, v[0] * s + v[1] * c)


def lead_point(src: List[float], tpos: List[float], tvel: List[float], speed: float) -> List[float]:
    """Where a straight shot at `speed` meets a target moving at constant velocity."""
    p = list(tpos)
    for _ in range(4):
        t = math.dist(src, p) / speed
        p = [tpos[i] + tvel[i] * t for i in range(3)]
    return p


def angles_to(src: List[float], p: List[float]) -> Vec:
    return (math.atan2(p[1] - src[1], p[0] - src[0]), math.atan2(p[2] - src[2], math.hypot(p[0] - src[0], p[1] - src[1])))


def mortar_pitch(d: float, dz: float, v: float, g: float) -> Optional[float]:
    """Low-arc launch pitch for a shell to land `d` m away and `dz` m up, or None if out of reach."""
    if d < 1e-6:
        return None
    disc = v ** 4 - g * (g * d * d + 2 * dz * v * v)
    return None if disc < 0 else math.atan2(v * v - math.sqrt(disc), g * d)


def mortar_aim(src: List[float], tpos: List[float], tvel: List[float], v: float, g: float) -> Optional[Tuple[float, float, float]]:
    p = list(tpos)
    for _ in range(4):
        d = math.hypot(p[0] - src[0], p[1] - src[1])
        th = mortar_pitch(d, p[2] - src[2], v, g)
        if th is None:
            return None
        tof = d / (v * math.cos(th))
        p = [tpos[i] + tvel[i] * tof for i in range(3)]
    d = math.hypot(p[0] - src[0], p[1] - src[1])
    th = mortar_pitch(d, p[2] - src[2], v, g)
    return None if th is None else (math.atan2(p[1] - src[1], p[0] - src[0]), th, d)


def zone_circle(z: Dict[str, Any], t: float) -> Tuple[Vec, float]:
    """The circle in force at match time t (linear shrink between shrink_start and shrink_end)."""
    a, b = z["shrink_start"], z["shrink_end"]
    f = 0.0 if t <= a else 1.0 if t >= b or b <= a else (t - a) / (b - a)
    c, n = z["center"], z["next_center"]
    return ((c[0] + (n[0] - c[0]) * f, c[1] + (n[1] - c[1]) * f), z["radius"] + (z["next_radius"] - z["radius"]) * f)


def pick_upgrade(offer: Dict[str, Any], defs: List[Dict[str, Any]], held: List[str]) -> int:
    by_id = {u["id"]: u for u in defs}
    best, best_i = -1.0, 0
    for i, ch in enumerate(offer["choices"]):
        s = 0.0
        for e in by_id.get(ch["id"], {}).get("effects", []):
            path = str(e.get("path", ""))
            if path.startswith("weapons."):
                s += WEAPON_SCORE if path.split(".")[1] in held else 0.5
            else:
                s += PATH_SCORE.get(path, 1)
        if s > best:
            best, best_i = s, i
    return best_i


def finite(v: Any) -> Any:
    return 0.0 if isinstance(v, float) and not math.isfinite(v) else v


class Bot:
    name = "safe-royale"

    def __init__(self, hello: mf.Hello) -> None:
        cfg = hello["config"]
        self.cfg = cfg
        self.rng = random.Random(hello.get("seed", 0))
        self.reach = float(cfg["loot"]["pickup_radius"])
        self.hist: "deque[Tuple[float, float]]" = deque(maxlen=12)
        self.cmd_len = 0.0
        self.stuck_until = -1.0
        self.stuck_rot = 0.0
        self.stuck_n = 0
        self.sign = 1.0
        self.flip_at = 0.0
        self.dmg: Optional[Tuple[float, float, Optional[float]]] = None  # time, bearing, attacker
        self.bad: set = set()
        self.errors = 0

    def act(self, obs: mf.Observation) -> mf.Action:
        try:
            return self._act(obs)  # type: ignore[return-value]
        except Exception:
            self.errors += 1
            if self.errors <= 3:
                mf.log(traceback.format_exc())
            z = obs["zone"]
            me = obs["self"]["pos"]
            v = unit(z["next_center"][0] - me[0], z["next_center"][1] - me[1])
            return {"tick": obs["tick"], "move": [v[0], v[1]], "boost": not obs["self"].get("in_zone", True)}

    def _act(self, obs: Dict[str, Any]) -> Dict[str, Any]:
        me, t, tick, z = obs["self"], float(obs["t"]), obs["tick"], obs["zone"]
        px, py, pz = me["pos"]
        st = me["stats"]
        ws: List[Optional[Dict[str, Any]]] = me["weapons"]
        held = [w["kind"] for w in ws if w]
        hpf = me["hp"] / max(me["max_hp"], 1.0)
        act: Dict[str, Any] = {"tick": tick}
        for e in obs["events"]:
            if e["type"] == "damaged" and e.get("by") is not None and e.get("bearing") is not None:
                self.dmg = (t, float(e["bearing"]), e["by"])
            elif e["type"] == "take_failed":
                self.bad.add(e["id"])
        if obs["offer"]:
            act["pick"] = pick_upgrade(obs["offer"], self.cfg["upgrades"], held)

        # ---- zone: head for a point well inside the next circle
        nc, nr = z["next_center"], z["next_radius"]
        dn = math.hypot(px - nc[0], py - nc[1])
        stop = max(nr * 0.5, 3.0)
        away_nc = unit(px - nc[0], py - nc[1])
        goal = (nc[0] + away_nc[0] * stop, nc[1] + away_nc[1] * stop) if dn > stop else (px, py)
        gdist = max(0.0, dn - stop)
        g = unit(goal[0] - px, goal[1] - py) if gdist > 2 else (0.0, 0.0)
        cc, cr = zone_circle(z, t)
        walk = st.get("walk_speed", 11.7)
        eta = gdist / max(walk, 1.0)
        urgent = (not me["in_zone"] or cr - math.hypot(px - cc[0], py - cc[1]) < 25
                  or (dn > nr * 0.92 and t >= z["shrink_start"] - 20) or eta > z["shrink_end"] - t - 10)

        # ---- targets
        foes = []
        for m in obs["mechs"]:
            if m.get("hp", 1) > 0:
                foes.append((math.dist(m["pos"], me["pos"]), m))
        foes.sort(key=lambda f: f[0])
        mechs = [f for f in foes if f[1]["kind"] == "mech"]
        npcs = [f for f in foes if f[1]["kind"] == "npc" and f[0] < 45]
        target = None
        if mechs:
            target = mechs[0]
            if self.dmg and t - self.dmg[0] < 4:
                for f in mechs:
                    if f[1]["id"] == self.dmg[2] and f[0] < 200:
                        target = f
        elif npcs and hpf > 0.5:
            target = npcs[0]
        close = [f for f in mechs if f[0] < 120]
        retreat = hpf < 0.4 or len(close) >= 3

        # ---- weapon pickup when it is calm and the spot is safe
        pk = None
        if not urgent and not retreat and hpf > 0.3 and None in ws and not (mechs and mechs[0][0] < 70):
            order = {"laser": 0, "lightning": 1, "mortar": 2}
            cand = [p for p in obs["pickups"] if p["kind"] in order and p["kind"] not in held and p["id"] not in self.bad
                    and math.hypot(p["pos"][0] - px, p["pos"][1] - py) < 40
                    and math.hypot(p["pos"][0] - nc[0], p["pos"][1] - nc[1]) < nr * 0.85]
            if cand:
                pk = min(cand, key=lambda p: math.hypot(p["pos"][0] - px, p["pos"][1] - py) + 8 * order[p["kind"]])
                if math.hypot(pk["pos"][0] - px, pk["pos"][1] - py) < self.reach - 0.7 and abs(pk["pos"][2] - pz) < 3.5:
                    act["take"] = pk["id"]

        # ---- movement
        if self.flip_at <= t:
            self.sign = -self.sign
            self.flip_at = t + 1.5 + self.rng.random() * 2.0
        v: Vec = g
        if urgent:
            v = g
        else:
            if pk:
                v = unit(pk["pos"][0] - px, pk["pos"][1] - py)
            if target and target[0] < 150:
                e = unit(target[1]["pos"][0] - px, target[1]["pos"][1] - py)
                perp = (-e[1] * self.sign, e[0] * self.sign)
                if retreat:
                    v = (-e[0] + g[0] * 0.6 + perp[0] * 0.3, -e[1] + g[1] * 0.6 + perp[1] * 0.3)
                else:
                    v = (v[0] + perp[0] * 0.9, v[1] + perp[1] * 0.9)
                    if target[0] < 20:
                        v = (v[0] - e[0] * 0.7, v[1] - e[1] * 0.7)
        n = math.hypot(*v)
        if n > 1:
            v = (v[0] / n, v[1] / n)
        # stuck: commanded to move but barely moved in a second
        self.hist.append((px, py))
        stuck = tick < self.stuck_until
        if not stuck and len(self.hist) == self.hist.maxlen and self.cmd_len > 0.5 and me["grounded"] \
                and math.dist(self.hist[0], self.hist[-1]) < 1.5:
            self.stuck_n += 1
            self.stuck_until = tick + 14
            self.stuck_rot = (math.pi / 2 + self.rng.random() * 0.7) * (1 if self.stuck_n % 2 else -1)
            self.hist.clear()
            stuck = True
            if pk:
                self.bad.add(pk["id"])  # unreachable from here, such as a weapon on a roof
        if stuck:
            base = v if math.hypot(*v) > 0.1 else (g if math.hypot(*g) > 0.1 else unit(nc[0] - px, nc[1] - py))
            v = rotate(base, self.stuck_rot)
            act["jump"] = bool(me["grounded"])
        boost = (urgent or stuck) and me["energy"] > (25 if stuck else 12)
        act["boost"] = boost or (stuck and not me["grounded"] and me["energy"] > 10)
        act["move"] = [v[0], v[1]]
        self.cmd_len = math.hypot(*v)

        # ---- aim and fire
        yaw, pitch = me["aim"]
        fire = [False, False]
        if target:
            d, m = target
            s = 1 + 0.05 * m.get("cubes", 0) if m["kind"] == "mech" else 1.0
            ctr = [m["pos"][0], m["pos"][1], m["pos"][2] + (4.5 * s if m["kind"] == "mech" else 1.75)]
            tv = m.get("vel", [0, 0, 0])
            src = [px, py, pz + st.get("muzzle_height", 7)]
            tube = [px, py, pz + st.get("height", 9) + 0.5]
            wc = self.cfg["weapons"]
            lo, hi = st.get("min_pitch", -0.7), st.get("max_pitch", 1.3)
            want: Dict[str, Optional[Tuple[float, float]]] = {}
            slots: Dict[str, int] = {}
            for i, w in enumerate(ws):
                if w:
                    slots[w["kind"]] = i
            gw = ws[slots["gun"]] if "gun" in slots else None
            if gw:
                want["gun"] = angles_to(src, lead_point(src, ctr, tv, gw["stats"]["speed"]))
            if "laser" in slots:
                want["laser"] = angles_to(src, ctr)
            if "lightning" in slots:
                want["lightning"] = angles_to(src, ctr)
            mw = ws[slots["mortar"]] if "mortar" in slots else None
            ma = mortar_aim(tube, ctr, tv, mw["stats"]["speed"], wc["mortar"]["gravity"]) if mw else None
            if ma:
                want["mortar"] = (ma[0], ma[1])
            # primary weapon picks the commanded aim
            prim = None
            lt = ws[slots["lightning"]]["stats"] if "lightning" in slots else None
            ls = ws[slots["laser"]] if "laser" in slots else None
            if lt and d <= lt["range"] * 0.9 and me["energy"] > 15:
                prim = "lightning"
            elif ls and d <= ls["stats"]["range"] * 0.95 and (ls.get("charge", 0) > 0 or ls["cooldown"] <= 0.8):
                prim = "laser"
            elif gw and d <= gw["stats"]["range"] * 0.95:
                prim = "gun"
            elif ma and 22 < ma[2]:
                prim = "mortar"
            aim = want.get(prim) if prim else None
            if aim is None:
                aim = (math.atan2(m["pos"][1] - py, m["pos"][0] - px), 0.0)
            yaw, pitch = aim[0], min(hi, max(lo, aim[1]))
            ay, ap = me["aim"]

            def err(w: Tuple[float, float]) -> float:
                return math.hypot(wrap(w[0] - ay) * math.cos(ap), w[1] - ap)

            tol = min(0.15, max(0.02, 2.2 / max(d, 1.0)))
            if gw and "gun" in want and d <= gw["stats"]["range"] * 0.95:
                fire[slots["gun"]] = gw.get("ammo", 1) > 0 and gw.get("reload", 0) <= 0 and err(want["gun"]) < tol
            if lt and "lightning" in want and d <= lt["range"] * 0.95 and me["energy"] > (10 if d < 20 else 25):
                fire[slots["lightning"]] = abs(wrap(want["lightning"][0] - ay)) < math.radians(lt["cone_deg"]) * 0.4
            if ls and "laser" in want:
                lc = ls.get("charge", 0.0)
                full = ls["stats"]["charge"]
                inr = d <= ls["stats"]["range"] * 0.95
                e = err(want["laser"])
                if not inr:
                    fire[slots["laser"]] = False
                elif 0 < lc < full * 0.95:
                    fire[slots["laser"]] = True
                elif lc >= full * 0.95:
                    fire[slots["laser"]] = e > tol  # release once lined up
                else:
                    fire[slots["laser"]] = ls["cooldown"] <= 0.1 and e < tol * 3
            if mw and ma and ma[2] > 20 and prim == "mortar":
                fire[slots["mortar"]] = err(want["mortar"]) < 0.04
        elif self.dmg and t - self.dmg[0] < 2.5:
            yaw, pitch = self.dmg[1], 0.0  # face where we were hit from: rear hits do 1.5x
        elif math.hypot(*v) > 0.1:
            yaw, pitch = math.atan2(v[1], v[0]), 0.0
        act["aim"] = [yaw, pitch]
        act["fire"] = fire
        gun = next((w for w in ws if w and w["kind"] == "gun"), None)
        if gun and gun.get("reload", 0) <= 0 and (gun.get("ammo", 1) <= 0 or (not target and gun.get("ammo", 1) < gun["stats"]["mag"] * 0.5)):
            act["reload"] = True
        for k, val in act.items():
            if isinstance(val, list):
                act[k] = [finite(x) for x in val]
            else:
                act[k] = finite(val)
        return act


if __name__ == "__main__":
    mf.run(Bot)
