"""Assert-based tests of the decision logic. Run: python3 tests/test_bot.py (from anywhere)."""
import copy
import gzip
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import mech_bot as mb  # noqa: E402

with gzip.open(os.path.join(HERE, "data", "stream.jsonl.gz"), "rt") as f:
    MSGS = [json.loads(line) for line in f]
HELLO = MSGS[0]
OBS = [m for m in MSGS if m["type"] == "obs"]
KEYS = {"tick", "move", "boost", "jump", "aim", "fire", "reload", "pick", "take", "drop", "debug"}


def base_obs(**over):
    o = copy.deepcopy(OBS[0])
    o["events"] = []
    o["mechs"] = []
    o["pickups"] = []
    o["self"]["pos"] = [0.0, 0.0, 0.0]
    o["self"]["aim"] = [0.0, 0.0]
    o["zone"].update(center=[0, 0], radius=560, next_center=[0, 0], next_radius=380, shrink_start=60, shrink_end=110)
    o.update(over)
    return o


def enemy(pos, vel=(0, 0, 0), **kw):
    m = {"id": 5, "kind": "mech", "pos": list(pos), "vel": list(vel), "aim": [0, 0], "hp": 1400, "max_hp": 1400}
    m.update(kw)
    return m


def weapon(kind, **kw):
    stats = copy.deepcopy(HELLO["config"]["weapons"][kind])
    w = {"kind": kind, "cooldown": 0, "stats": stats}
    if kind == "gun":
        w.update(ammo=12, reload=0)
    if kind == "laser":
        w.update(charge=0)
    w.update(kw)
    return w


def valid(a, tick):
    assert a["tick"] == tick and set(a) <= KEYS, a
    assert math.hypot(*a["move"]) <= 1.0001 and len(a["fire"]) == 2
    assert all(math.isfinite(x) for x in a["move"] + a["aim"])


def test_whole_stream():
    bot = mb.Bot(HELLO)
    worst = 0.0
    for o in OBS:
        t0 = time.perf_counter()
        a = bot.act(o)
        worst = max(worst, time.perf_counter() - t0)
        valid(a, o["tick"])
    assert worst < 0.02, worst


def test_lead_maths():
    src = [0.0, 0.0, 7.0]
    p = mb.lead_point(src, [100.0, 0.0, 7.0], [0.0, 20.0, 0.0], 252.0)
    t = math.dist(src, p) / 252.0
    assert abs(p[1] - 20.0 * t) < 1e-6 and p[1] > 5
    yaw, pitch = mb.angles_to(src, [0.0, 10.0, 17.0])
    assert abs(yaw - math.pi / 2) < 1e-9 and abs(pitch - math.pi / 4) < 1e-9


def test_mortar_ballistics():
    v, g = 72.0, 22.0
    th = mb.mortar_pitch(100.0, 0.0, v, g)
    assert th is not None
    tof = 100.0 / (v * math.cos(th))
    assert abs(v * math.sin(th) * tof - 0.5 * g * tof * tof) < 1e-6  # lands at the same height
    assert mb.mortar_pitch(300.0, 0.0, v, g) is None  # beyond ~235 m


def test_zone_circle_and_goal():
    z = {"center": [0, 0], "radius": 500, "next_center": [100, 0], "next_radius": 300, "shrink_start": 10, "shrink_end": 20}
    assert mb.zone_circle(z, 0) == ((0, 0), 500) and mb.zone_circle(z, 25) == ((100, 0), 300)
    assert mb.zone_circle(z, 15) == ((50, 0), 400)
    bot = mb.Bot(HELLO)
    o = base_obs()
    o["self"]["pos"] = [-300.0, 0.0, 0.0]
    a = bot.act(o)
    assert a["move"][0] > 0.9  # toward the next circle
    o = base_obs()
    o["self"]["pos"] = [500.0, 0.0, 0.0]
    o["self"]["in_zone"] = False
    a = bot.act(o)
    assert a["boost"] and a["move"][0] < -0.9  # outside: run in, boosting
    o = base_obs()
    o["self"]["pos"] = [10.0, 0.0, 0.0]
    o["self"]["weapons"] = [weapon("gun"), weapon("laser", charge=0.0)]
    o["self"]["cubes"] = 10
    a = bot.act(o)
    assert a["move"] == [0.0, 0.0]  # safely inside and stocked: stay put
    o["self"]["weapons"] = [weapon("gun"), None]
    assert bot.act(o)["move"] != [0.0, 0.0]  # no laser: scout for loot


def test_loot_memory_and_pick():
    from loot import Loot
    lt = Loot(None)
    lt.update({"crates": [{"id": 1, "pos": [50.0, 0.0, 0.0]}], "cubes": [{"id": 2, "pos": [-90.0, 0.0, 0.0]}]}, (0.0, 0.0), [0.0, 0.0, 8.0])
    ok = lambda x, y: True  # noqa: E731
    assert lt.pick((0.0, 0.0), False, ok, set()) == (50.0, 0.0) and lt.goal == ("crates", 1)
    lt.update({"crates": [], "cubes": []}, (0.0, 0.0), [0.0, 0.0, 8.0])  # both within 40 m? crate at 50 stays, cube at 90 stays
    assert lt.pick((0.0, 0.0), False, ok, {1}) == (-90.0, 0.0)
    lt.update({"crates": [], "cubes": []}, (45.0, 0.0), [45.0, 0.0, 8.0])  # crate now in sight and absent: forgotten
    assert 1 not in lt.mem["crates"]
    assert lt.pick((45.0, 0.0), True, lambda x, y: x > 0, set()) is not None  # nothing left to the east of home: scout


def test_aim_gate_and_fire():
    bot = mb.Bot(HELLO)
    o = base_obs(mechs=[enemy([100, 0, 0])])
    o["self"]["weapons"] = [weapon("gun"), None]
    o["self"]["aim"] = [0.0, 0.0]
    # torso 1 rad off the lead aim must not fire
    o["self"]["aim"] = [1.0, 0.0]
    assert bot.act(o)["fire"] == [False, False]
    o["self"]["weapons"][0].update(ammo=0)
    o["self"]["aim"] = [0.0, 0.0]
    a = bot.act(o)
    assert a["fire"][0] is False and a.get("reload") is True


def test_gun_fires_when_exactly_aligned():
    bot = mb.Bot(HELLO)
    o = base_obs(mechs=[enemy([100, 0, 3.5 - 4.5 + 7.0 - 7.0])])
    o["self"]["weapons"] = [weapon("gun"), None]
    yaw, pitch = mb.angles_to([0, 0, 7.0], [100, 0, 4.5 + o["mechs"][0]["pos"][2]])
    o["self"]["aim"] = [yaw, pitch]
    assert bot.act(o)["fire"][0] is True


def test_laser_charge_release():
    bot = mb.Bot(HELLO)
    o = base_obs(mechs=[enemy([100, 0, 0])])
    yaw, pitch = mb.angles_to([0, 0, 7.0], [100, 0, 4.5])
    o["self"]["aim"] = [yaw, pitch]
    o["self"]["weapons"] = [weapon("gun"), weapon("laser", charge=0.0)]
    assert bot.act(o)["fire"][1] is True  # start charging
    o["self"]["weapons"][1]["charge"] = 0.3
    assert bot.act(o)["fire"][1] is True  # keep holding
    o["self"]["weapons"][1]["charge"] = 0.75
    assert bot.act(o)["fire"][1] is False  # aligned and full: let go
    o["self"]["weapons"][1].update(charge=0.0, cooldown=1.2)
    assert bot.act(o)["fire"][1] is False  # cooling down


def test_lightning_cone():
    bot = mb.Bot(HELLO)
    o = base_obs(mechs=[enemy([30, 0, 0])])
    o["self"]["weapons"] = [weapon("gun"), weapon("lightning")]
    o["self"]["aim"] = [0.0, 0.0]
    assert bot.act(o)["fire"][1] is True
    o["self"]["aim"] = [1.0, 0.0]  # far outside the 30 degree cone
    assert bot.act(o)["fire"][1] is False
    o = base_obs(mechs=[enemy([80, 0, 0])])
    o["self"]["weapons"] = [weapon("gun"), weapon("lightning")]
    assert bot.act(o)["fire"][1] is False  # out of 45 m


def test_mortar_pitch_commanded():
    bot = mb.Bot(HELLO)
    o = base_obs(mechs=[enemy([225, 0, 0])])
    o["self"]["weapons"] = [weapon("gun"), weapon("mortar")]
    a = bot.act(o)
    assert 0.2 < a["aim"][1] < 1.3  # lobbed, not flat


def test_pickups():
    bot = mb.Bot(HELLO)
    o = base_obs(pickups=[{"id": 8001, "kind": "mortar", "pos": [2.0, 0.0, 0.0]}])
    assert bot.act(o)["take"] == 8001
    o["self"]["weapons"] = [weapon("gun"), weapon("mortar")]
    assert "take" not in bot.act(o)  # full slots and already held
    o["self"]["weapons"] = [weapon("gun"), None]
    o["pickups"][0]["pos"] = [9.0, 0.0, 0.0]
    a = bot.act(o)
    assert "take" not in a and a["move"][0] > 0.9  # walks over, does not take from range
    o["pickups"][0]["pos"] = [2.0, 0.0, 0.0]
    o["events"] = [{"type": "take_failed", "id": 8001, "reason": "range"}]
    assert "take" not in bot.act(o)  # blacklisted
    o = base_obs(pickups=[{"id": 8002, "kind": "gun", "pos": [2.0, 0.0, 0.0]}])
    assert "take" not in bot.act(o)
    o = base_obs(pickups=[{"id": 8003, "kind": "laser", "pos": [20.0, 0.0, 0.0]}], mechs=[enemy([-30, 0, 0])])
    assert "take" not in bot.act(o)  # enemy close: not safe


def test_offer_prefers_damage_then_survival():
    defs = HELLO["config"]["upgrades"]
    offer = {"id": 1, "expires": 9, "choices": [{"id": "gun_rate", "name": "", "desc": ""}, {"id": "armor", "name": "", "desc": ""},
                                                 {"id": "sensors", "name": "", "desc": ""}]}
    assert mb.pick_upgrade(offer, defs, ["gun"]) == 0  # damage upgrades first
    offer["choices"][0]["id"] = "sensors"
    assert mb.pick_upgrade(offer, defs, ["gun"]) == 1  # survival beats the rest
    offer["choices"] = [{"id": "mystery", "name": "", "desc": ""}, {"id": "gun_damage", "name": "", "desc": ""}]
    assert mb.pick_upgrade(offer, defs, ["gun"]) == 1  # unknown id never crashes
    o = base_obs(offer=offer)
    assert bot_pick(o) in (0, 1)


def bot_pick(o):
    return mb.Bot(HELLO).act(o)["pick"]


def test_retreat_and_face_attacker():
    bot = mb.Bot(HELLO)
    o = base_obs(mechs=[enemy([60, 0, 0])])
    o["self"]["hp"] = 300
    a = bot.act(o)
    assert a["move"][0] < -0.3  # backs away
    o = base_obs()
    o["events"] = [{"type": "damaged", "amount": 50, "weapon": "gun", "bearing": 2.0, "by": 7, "rear": True}]
    a = bot.act(o)
    assert abs(a["aim"][0] - 2.0) < 1e-9  # turns to face the shooter


def test_stuck_escape():
    bot = mb.Bot(HELLO)
    o = base_obs()
    o["self"]["pos"] = [-300.0, 0.0, 0.0]
    jumped = False
    for i in range(30):
        o["tick"] = i * 2
        a = bot.act(o)
        jumped = jumped or a.get("jump") is True
    assert jumped


def test_unreachable_pickup_is_dropped():
    bot = mb.Bot(HELLO)
    o = base_obs(pickups=[{"id": 8010, "kind": "laser", "pos": [9.0, 0.0, 20.0]}])
    o["self"]["weapons"] = [weapon("gun"), None]
    o["self"]["grounded"] = True
    for i in range(14):
        o["tick"] = i * 2
        bot.act(o)
    assert 8010 in bot.bad


def test_bad_input_never_raises():
    bot = mb.Bot(HELLO)
    a = bot.act({"tick": 4, "zone": {"next_center": [0, 0]}, "self": {"pos": [1, 1, 1]}})
    assert a["tick"] == 4


if __name__ == "__main__":
    names = [n for n in sorted(globals()) if n.startswith("test_")]
    for n in names:
        globals()[n]()
        print("ok", n)
    print(f"{len(names)} tests passed")
