"""Safe Mech Royale bot (mechfight/1): survive the zone first, shoot what is in front of us."""
from __future__ import annotations

import math
import random
import traceback
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

import mechfight as mf
from dodge import dodge_vec
from loot import Loot
from nav import Nav, Walker

Vec = Tuple[float, float]
TAU = 2 * math.pi
# Offer scoring by effect path: survival first. Unknown paths score 1, so new upgrades still rank.
DMG_FIRST = {"laser_damage": 30, "gun_damage": 29, "gun_rate": 28, "laser_charge": 20}
PATH_SCORE = {"mech.armor": 10, "mech.hp": 9, "mech.regen_per_s": 8, "mech.regen_delay": 6,
              "mech.walk_speed": 7, "mech.boost_speed": 2, "mech.energy": 5, "mech.energy_regen": 4,
              "mech.boost_drain": 3, "mech.jet_drain": 1, "mech.turn_rate": 5, "mech.pitch_rate": 1,
              "mech.near_range_bonus": 3, "mech.vision_bonus": 2, "mech.hearing": 1}
FLEE_R, GAP_IN, GAP_OUT, FLEE_E = 170.0, 60.0, 90.0, 15.0
NEAR_W = 8.0
DODGE_K = 0.6
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


def finite(v: Any) -> Any:
    return 0.0 if isinstance(v, float) and not math.isfinite(v) else v


class Bot:
    name = "toyo-royale"

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
        self.nav: Optional[Nav] = None
        self.walker: Optional[Walker] = None
        try:
            self.nav = Nav(str(hello["map"]["dir"]))
            self.walker = Walker(self.nav)
        except (OSError, ValueError, KeyError, IndexError, TypeError) as e:
            mf.log(f"map unavailable, steering straight: {e}")
        self.retreating = False
        self.fleeing = False
        self.target_id: Optional[float] = None
        self.cover: Optional[Vec] = None
        self.cover_tick = -99.0
        self.loot_id: Optional[float] = None
        self.known: Dict[int, Dict[str, Any]] = {}  # weapons never despawn, so remember them
        self.loot = Loot(self.nav)

    def _steer(self, here: Vec, goal: Vec) -> Vec:
        if math.hypot(goal[0] - here[0], goal[1] - here[1]) < 1.5:
            return (0.0, 0.0)
        return self.walker.step(here, goal) if self.walker else unit(goal[0] - here[0], goal[1] - here[1])

    def _pick_target(self, mechs: List[Any], npcs: List[Any], hpf: float, eye: List[float], t: float) -> Any:
        """Prefer a mech we can hit, then the weakest and nearest, and stay on the current one."""
        if not mechs:
            self.target_id = None
            return npcs[0] if npcs and hpf > 0.5 else None

        multi = sum(1 for f in mechs if f[0] < FLEE_R) >= 2

        def rank(f: Any) -> float:
            d, m = f
            r = float(m.get("hp", 1400)) + (NEAR_W if multi else 3.0) * d
            if self.nav and not self.nav.sees(eye, [m["pos"][0], m["pos"][1], m["pos"][2] + 4.5]):
                r += 900
            if self.dmg and t - self.dmg[0] < 4 and m["id"] == self.dmg[2]:
                r -= 250
            return r - 200 if m["id"] == self.target_id else r

        best = min(mechs, key=rank)
        self.target_id = best[1]["id"]
        return best

    def _hide(self, me: List[float], mechs: List[Any], tick: float) -> Optional[Vec]:
        if self.nav is None:
            return None
        if tick - self.cover_tick >= 10:
            self.cover = self.nav.cover_from(me, [f[1]["pos"] for f in mechs[:4]])
            self.cover_tick = tick
        return self.cover

    def _loot_goal(self, obs: Dict[str, Any], pk: Any, here: Vec, mechs: List[Any], hpf: float, t: float) -> Optional[Vec]:
        """Supply drops first (full repair), then weapons, cubes and crates when nothing is close."""
        px, py = here
        me = obs["self"]
        self.loot_id = None
        speed = max(me["stats"].get("boost_speed", 19.5), 1.0)
        best = None
        for d in obs.get("drops", []):
            dist = math.hypot(d["pos"][0] - px, d["pos"][1] - py)
            contested = any(math.hypot(f[1]["pos"][0] - d["pos"][0], f[1]["pos"][1] - d["pos"][1]) < d["radius"] + 6 for f in mechs)
            if d.get("holder") in (None, me["id"]) and not contested and dist < 220 and dist / speed < d["lands_in"] + 20 \
                    and d["id"] not in self.bad and (best is None or dist < best[0]):
                best = (dist, d)
        if best:
            self.loot_id = best[1]["id"]
            return here if best[0] <= best[1]["radius"] * 0.55 else (best[1]["pos"][0], best[1]["pos"][1])
        if hpf < 0.6 or (mechs and mechs[0][0] < 60):
            return None
        if pk:
            return (pk["pos"][0], pk["pos"][1])
        need = "laser" not in [w["kind"] for w in me["weapons"] if w] or me.get("cubes", 0) < min(10, t / 25.0)
        z = obs["zone"]
        cc, cr = zone_circle(z, t)
        nc, nr = z["next_center"], z["next_radius"]
        if t < z["shrink_start"] - 25:
            ok = lambda x, y: math.hypot(x - cc[0], y - cc[1]) < cr * 0.85  # noqa: E731
        else:
            ok = lambda x, y: math.hypot(x - nc[0], y - nc[1]) < nr * 0.8  # noqa: E731
        spot = self.loot.pick(here, need and not (mechs and mechs[0][0] < 110), ok, self.bad, (nc[0], nc[1]))
        if spot is None:
            return None
        if self.loot.goal is not None:
            self.loot_id = self.loot.goal[1]
            if self.loot.goal[0] == "crates" and math.hypot(spot[0] - px, spot[1] - py) < 22 and self.nav \
                    and self.nav.sees([px, py, me["pos"][2] + 8], [spot[0], spot[1], me["pos"][2] + 1.2]):
                return here
        return spot

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
        seen_ids = {p["id"] for p in obs["pickups"]}
        self.known.update({p["id"]: p for p in obs["pickups"]})
        self.loot.update(obs, (px, py), [px, py, pz + st.get("eye_height", 8)])
        for i, p in list(self.known.items()):
            if i in self.bad or (i not in seen_ids and math.hypot(p["pos"][0] - px, p["pos"][1] - py) < 30):
                del self.known[i]
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
        eye = [px, py, pz + st.get("eye_height", 8)]
        foes = []
        for m in obs["mechs"]:
            if m.get("hp", 1) > 0:
                foes.append((math.dist(m["pos"], me["pos"]), m))
        foes.sort(key=lambda f: f[0])
        mechs = [f for f in foes if f[1]["kind"] == "mech"]
        npcs = [f for f in foes if f[1]["kind"] == "npc" and f[0] < 60]
        target = self._pick_target(mechs, npcs, hpf, eye, t)
        close = [f for f in mechs if f[0] < 120]
        self.retreating = bool(mechs) and hpf < (0.65 if self.retreating else 0.4)
        retreat = self.retreating or hpf < 0.4
        # string the swarm out: with 2+ hunters bunched together, run so the faster/earlier one arrives alone
        thr = [f for f in mechs if f[0] < FLEE_R]
        gap = (thr[1][0] - thr[0][0]) if len(thr) >= 2 else 999.0
        was = self.fleeing
        self.fleeing = len(thr) >= 2 and gap < (GAP_OUT if was else GAP_IN) and not retreat

        # ---- weapon pickup when it is calm and the spot is safe
        pk = None
        order = {"laser": 0, "lightning": 1, "mortar": 2}
        swap = None
        if None not in ws:
            extra = [i for i, w in enumerate(ws) if w and w["kind"] != "gun" and not w.get("charge")]
            swap = max(extra, key=lambda i: order.get(ws[i]["kind"], 3)) if extra else None
        if not urgent and not retreat and hpf > 0.3 and (None in ws or swap is not None):
            limit = order.get(ws[swap]["kind"], 3) if swap is not None else 9
            reach_by = {"laser": 190.0 if self.nav else 40.0, "lightning": 90.0 if self.nav else 40.0, "mortar": 40.0}
            near = mechs[0][0] if mechs else 999.0
            cand = [p for p in self.known.values() if p["kind"] in order and order[p["kind"]] < limit and p["kind"] not in held
                    and p["id"] not in self.bad and math.hypot(p["pos"][0] - px, p["pos"][1] - py) < reach_by[p["kind"]]
                    and (near > 40 or math.hypot(p["pos"][0] - px, p["pos"][1] - py) < 15)
                    and (math.hypot(p["pos"][0] - cc[0], p["pos"][1] - cc[1]) < cr * 0.9 if t < z["shrink_start"] - 25
                         else math.hypot(p["pos"][0] - nc[0], p["pos"][1] - nc[1]) < nr * 0.85)]
            if cand:
                pk = min(cand, key=lambda p: math.hypot(p["pos"][0] - px, p["pos"][1] - py) + 60 * order[p["kind"]])
                if math.hypot(pk["pos"][0] - px, pk["pos"][1] - py) < self.reach - 0.7 and abs(pk["pos"][2] - pz) < 3.5:
                    act["take"] = pk["id"]
                    if swap is not None:
                        act["drop"] = swap

        # ---- movement
        if self.flip_at <= t:
            self.sign = -self.sign
            self.flip_at = t + 1.5 + self.rng.random() * 2.0
        here = (px, py)
        zone_step = self._steer(here, goal) if gdist > 2 else (0.0, 0.0)
        v: Vec = zone_step
        holding = False
        self.loot_id = None
        flee_boost = False
        if self.fleeing and not urgent:
            ax = sum((px - f[1]["pos"][0]) / max(f[0], 1.0) ** 2 for f in thr)
            ay_ = sum((py - f[1]["pos"][1]) / max(f[0], 1.0) ** 2 for f in thr)
            away = unit(ax, ay_)
            best_s = -9.0
            for k in range(16):
                a = k * TAU / 16
                dv = (math.cos(a), math.sin(a))
                run = 0
                for r in (8, 16, 24, 32, 44):
                    if self.nav and not self.nav.free(px + dv[0] * r, py + dv[1] * r):
                        break
                    run = r
                sc = dv[0] * away[0] + dv[1] * away[1] + 0.6 * (dv[0] * g[0] + dv[1] * g[1]) + run / 44.0
                if sc > best_s:
                    best_s, v = sc, dv
            flee_boost = me["energy"] > FLEE_E and thr[0][0] < 140
        elif not urgent:
            spot = None if retreat else self._loot_goal(obs, pk, here, mechs, hpf, t)
            holding = spot == here
            if spot is not None:
                v = self._steer(here, spot)
            elif not mechs and hpf < 0.6:
                v = (0.0, 0.0)  # nobody in view: stand still and let regen work
            if target and target[1]["kind"] == "mech" and target[0] < 150 and not holding:
                e = unit(target[1]["pos"][0] - px, target[1]["pos"][1] - py)
                perp = (-e[1] * self.sign, e[0] * self.sign)
                if self.nav and not self.nav.free(px + perp[0] * 6, py + perp[1] * 6):
                    self.sign, perp = -self.sign, (-perp[0], -perp[1])
                if retreat:
                    hide = self._hide(me["pos"], mechs, tick)
                    v = self._steer(here, hide) if hide else (-e[0] + g[0] * 0.6 + perp[0] * 0.3, -e[1] + g[1] * 0.6 + perp[1] * 0.3)
                else:
                    want = 90.0 if "mortar" in held else 32.0 if "lightning" in held else 70.0
                    pull = 0.5 if target[0] > want + 15 else -0.7 if target[0] < min(want - 12, 20.0) else 0.0
                    v = (v[0] + perp[0] * 0.9 + e[0] * pull, v[1] + perp[1] * 0.9 + e[1] * pull)
        dv = dodge_vec([px, py, pz + 4.5], obs.get("projectiles", []), mechs, self.sign)
        dn_ = math.hypot(*dv)
        if dn_ > 0:
            k = min(1.0, dn_) * DODGE_K / dn_ if dn_ > 1 else DODGE_K / dn_ * min(dn_, 1.0)
            v = (v[0] + dv[0] * k * 2, v[1] + dv[1] * k * 2)
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
            if self.loot_id is not None:
                self.bad.add(self.loot_id)
                self.loot.forget(self.loot_id)
            if self.walker:
                self.walker.reset()
        if stuck:
            base = v if math.hypot(*v) > 0.1 else (g if math.hypot(*g) > 0.1 else unit(nc[0] - px, nc[1] - py))
            v = rotate(base, self.stuck_rot)
            act["jump"] = bool(me["grounded"])
        rushing = self.loot_id is not None and self.loot_id >= 20000 and not holding and me["energy"] > 40
        boost = ((urgent or stuck) and me["energy"] > (25 if stuck else 12)) or rushing or flee_boost
        act["boost"] = boost or (stuck and not me["grounded"] and me["energy"] > 10)
        act["move"] = [v[0], v[1]]
        self.cmd_len = math.hypot(*v)

        # ---- aim and fire
        heard = [s for s in obs.get("sounds", []) if s.get("dist") != "far"
                 and s.get("kind") in ("gun", "mortar", "laser", "laser_charge", "lightning", "step", "boost")]
        yaw, pitch = me["aim"]
        fire = [False, False]
        if target is None and not urgent and not retreat and self.nav:
            near_crates = [c for c in obs.get("crates", []) if math.dist(c["pos"], me["pos"]) < 70
                           and self.nav.sees(eye, [c["pos"][0], c["pos"][1], c["pos"][2] + 1.2])]
            if near_crates:
                c = min(near_crates, key=lambda i: math.dist(i["pos"], me["pos"]))
                target = (math.dist(c["pos"], me["pos"]), {"id": c["id"], "kind": "crate", "pos": c["pos"], "vel": [0, 0, 0]})
        if target:
            d, m = target
            s = 1 + 0.05 * m.get("cubes", 0) if m["kind"] == "mech" else 1.0
            ctr = [m["pos"][0], m["pos"][1], m["pos"][2] + (4.5 * s if m["kind"] == "mech" else 1.75)]
            tv = m.get("vel", [0, 0, 0])
            src = [px, py, pz + st.get("muzzle_height", 7)]
            tube = [px, py, pz + st.get("height", 9) + 0.5]
            wc = self.cfg["weapons"]
            lo, hi = st.get("min_pitch", -0.7), st.get("max_pitch", 1.3)
            clear = self.nav is None or self.nav.sees(src, ctr)
            soft = m["kind"] == "crate"  # crates get the gun and lightning only
            want: Dict[str, Optional[Tuple[float, float]]] = {}
            slots: Dict[str, int] = {}
            for i, w in enumerate(ws):
                if w:
                    slots[w["kind"]] = i
            gw = ws[slots["gun"]] if "gun" in slots else None
            if gw:
                want["gun"] = angles_to(src, lead_point(src, ctr, tv, gw["stats"]["speed"]))
            if "laser" in slots and not soft:
                want["laser"] = angles_to(src, ctr)
            if "lightning" in slots:
                want["lightning"] = angles_to(src, ctr)
            mw = ws[slots["mortar"]] if "mortar" in slots else None
            ma = mortar_aim(tube, ctr, tv, mw["stats"]["speed"], wc["mortar"]["gravity"]) if mw and not soft else None
            if ma:
                want["mortar"] = (ma[0], ma[1])
            # primary weapon picks the commanded aim
            prim = None
            lt = ws[slots["lightning"]]["stats"] if "lightning" in slots else None
            ls = ws[slots["laser"]] if "laser" in slots else None
            if ma and m["kind"] == "mech" and 30 < ma[2] < 200 and mw["cooldown"] <= 0.3:
                prim = "mortar"
            elif lt and d <= lt["range"] * 0.9 and me["energy"] > 15:
                prim = "lightning"
            elif ls and not soft and clear and d <= ls["stats"]["range"] * 0.95 and (ls.get("charge", 0) > 0 or ls["cooldown"] <= 0.8):
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
                fire[slots["gun"]] = clear and gw.get("ammo", 1) > 0 and gw.get("reload", 0) <= 0 and err(want["gun"]) < tol
            if lt and "lightning" in want and d <= lt["range"] * 0.95 and me["energy"] > (10 if d < 20 else 25):
                fire[slots["lightning"]] = clear and abs(wrap(want["lightning"][0] - ay)) < math.radians(lt["cone_deg"]) * 0.4
            if ls and "laser" in want:
                lc = ls.get("charge", 0.0)
                full = ls["stats"]["charge"]
                inr = clear and d <= ls["stats"]["range"] * 0.95
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
        elif heard:
            yaw, pitch = heard[-1]["bearing"], 0.0  # look toward nearby fighting or footsteps
        else:
            yaw, pitch = wrap(me["aim"][0] + 0.38), 0.0  # idle: sweep the 120-degree vision wedge all the way round
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
