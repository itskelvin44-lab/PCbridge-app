"""Offline test of BridgeCore. Run from the project folder:  python tests/test_core.py"""
import asyncio, os, secrets, sys, types
sys.path.insert(0, os.path.dirname(__file__))
from fakes import *
import core as C
from protocol import *

fails = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails.append(name)

class Phone:
    """Plays the phone's side of the protocol against a FakeServer."""
    def __init__(self, srv, code):
        self.srv, self.code, self.seq, self.key = srv, code.encode(), 0, None
    def write(self, data): self.srv.write_request_func(self.srv.chars[RX_UUID], bytearray(data))
    def login(self, code=None):
        code = (code or self.code.decode()).encode()
        np_ = secrets.token_bytes(8)
        self.write(bytes([T_HELLO, 1]) + np_)
        ch = self.srv.sent[-1]
        if ch[0] != T_CHALLENGE: return ch      # locked: refused already at HELLO
        nl = ch[2:10]
        self.write(bytes([T_AUTH, 2]) + mac(code, b"P" + np_ + nl)[:8])
        r = self.srv.sent[-1]
        if r[0] == T_AUTH_OK:
            self.key = mac(code, b"K" + np_ + nl); self.seq = 2
        return r
    def send(self, t, payload=b"", key=None, seq=None):
        if seq is None: self.seq = (self.seq + 1) & 0xFF; seq = self.seq
        k = key or self.key
        self.write(bytes([t, seq]) + payload + make_tag(k, b"P", t, seq, payload))

async def main():
    events = []
    core = C.BridgeCore(lambda n, **d: events.append((n, d)), verbose=True)
    check("start() succeeds", await core.start())
    names = [n for n, _ in events]
    check("emits code, state=waiting, injection on",
          "code" in names and ("state", {"value": "waiting"}) in events and ("injection", {"value": True}) in events)
    code = core.code; check("code is 4 digits", len(code) == 4 and code.isdigit())
    srv = FakeServer.last; ph = Phone(srv, code)

    # wrong code
    r = ph.login("0000" if code != "0000" else "1111")
    check("wrong code -> AUTH_FAIL reason 0, 2 left", r[0] == T_AUTH_FAIL and r[2:4] == bytes([0, 2]))
    check("wrong code emits auth event", ("auth", {"kind": "wrong_code", "value": 2}) in events)
    check("state back to waiting after wrong code", core.state == "waiting")

    # good login
    r = ph.login()
    check("login -> AUTH_OK", r[0] == T_AUTH_OK)
    check("state connected", core.state == "connected")

    # heartbeat / ping
    ph.send(T_HEARTBEAT); check("heartbeat -> ACK", srv.sent[-1][0] == T_ACK)
    ph.send(T_PING); check("ping -> PONG", srv.sent[-1][0] == T_PONG)

    kb, ms, pd = "PCBridge Keyboard", "PCBridge Mouse", "PCBridge Gamepad"
    for d in (kb, ms, pd): EVENTS[d].clear()
    K = lambda n: getattr(ec, n)
    ph.send(T_KEY_DOWN, bytes([0xE1])); ph.send(T_KEY_DOWN, bytes([0x04]))
    ph.send(T_KEY_UP, bytes([0x04])); ph.send(T_KEY_UP, bytes([0xE1]))
    check("Shift+a down/up reaches keyboard",
          EVENTS[kb] == [(K("EV_KEY"), K("KEY_LEFTSHIFT"), 1), (K("EV_KEY"), K("KEY_A"), 1),
                         (K("EV_KEY"), K("KEY_A"), 0), (K("EV_KEY"), K("KEY_LEFTSHIFT"), 0)])
    EVENTS[kb].clear(); ph.send(T_KEY_TAP, bytes([0x2A]))
    check("tap Backspace = down+up", EVENTS[kb] == [(K("EV_KEY"), K("KEY_BACKSPACE"), 1), (K("EV_KEY"), K("KEY_BACKSPACE"), 0)])
    EVENTS[kb].clear(); ph.send(T_KEY_UP, bytes([0x05]))
    check("key_up of never-pressed key ignored", EVENTS[kb] == [])

    ph.send(T_MOUSE_MOVE, (10).to_bytes(2, "little", signed=True) + (-5).to_bytes(2, "little", signed=True))
    check("mouse move", (K("EV_REL"), K("REL_X"), 10) in EVENTS[ms] and (K("EV_REL"), K("REL_Y"), -5) in EVENTS[ms])
    ph.send(T_MOUSE_BTN, bytes([1, 2])); ph.send(T_MOUSE_WHEEL, bytes([3]))
    check("mouse click + wheel", (K("EV_KEY"), K("BTN_LEFT"), 1) in EVENTS[ms] and (K("EV_REL"), K("REL_WHEEL"), 3) in EVENTS[ms])

    mask = 1 | (1 << PAD_BITS.index("Left"))
    ph.send(T_GAMEPAD_STATE, bytes([mask & 255, (mask >> 8) & 255, 0, 127, 127, 0, 0]))
    check("gamepad A + hat left + stick",
          (K("EV_KEY"), K("BTN_SOUTH"), 1) in EVENTS[pd] and (K("EV_ABS"), K("ABS_HAT0X"), -1) in EVENTS[pd]
          and (K("EV_ABS"), K("ABS_X"), 32767) in EVENTS[pd])

    # held things released on RELEASE_ALL; held event lists them
    ph.send(T_KEY_DOWN, bytes([0xE0])); ph.send(T_MOUSE_BTN, bytes([1, 1]))
    events.clear(); ph.send(T_RELEASE_ALL)
    held = [d["value"] for n, d in events if n == "held"][-1]
    check("RELEASE_ALL reports held keys/buttons", "leftctrl" in held and "mouse:left" in held and "pad:A" in held)
    check("ctrl released on the device", (K("EV_KEY"), K("KEY_LEFTCTRL"), 0) in EVENTS[kb])

    # bad signature, replay
    before = len(EVENTS[kb]); ph.send(T_KEY_TAP, bytes([0x04]), key=b"x" * 32)
    check("bad signature rejected", len(EVENTS[kb]) == before)
    ph.send(T_KEY_TAP, bytes([0x04])); n1 = len(EVENTS[kb])
    ph.send(T_KEY_TAP, bytes([0x04]), seq=ph.seq); check("replayed sequence rejected", len(EVENTS[kb]) == n1)

    # emergency stop
    ph.send(T_KEY_DOWN, bytes([0x04])); core._set_injection(False)
    check("stop emits injection False", ("injection", {"value": False}) in events)
    check("stop released the held key", (K("EV_KEY"), K("KEY_A"), 0) in EVENTS[kb])
    n = len(EVENTS[kb]); ph.send(T_KEY_TAP, bytes([0x05]))
    check("input ignored while stopped", len(EVENTS[kb]) == n)
    core._set_injection(True)

    # text both ways
    core._send_text("hello"); check("send_text -> signed TEXT to phone", srv.sent[-1][0] == T_TEXT)

    # watchdog: phone silent
    ph.send(T_KEY_DOWN, bytes([0x06])); events.clear()
    core.last_heard -= PHONE_TIMEOUT + 1
    await asyncio.sleep(0.7)
    check("watchdog: state lost", core.state == "lost" and ("state", {"value": "lost"}) in events)
    check("watchdog: key released", (K("EV_KEY"), K("KEY_C"), 0) in EVENTS[kb])
    n = len(EVENTS[kb]); ph.send(T_KEY_TAP, bytes([0x04])); check("messages after session end ignored", len(EVENTS[kb]) == n)

    # lockout
    ph = Phone(srv, core.code); old = core.code; events.clear()
    for i in range(3): r = ph.login("9999" if old != "9999" else "8888")
    check("3 wrong codes -> locked", r[0] == T_AUTH_FAIL and r[2] == 1 and ("auth", {"kind": "locked", "value": 60}) in events)
    check("lockout makes a new code", any(n == "code" for n, _ in events))
    r = Phone(srv, core.code).login()
    check("correct code refused while locked", r[0] == T_AUTH_FAIL and r[2] == 1)

    await core.shutdown()
    check("shutdown twice is safe", (await core.shutdown()) is None)

    # failure paths
    ev2 = []; c2 = C.BridgeCore(lambda n, **d: ev2.append((n, d)))
    FakeServer.fail = RuntimeError("No Bluetooth adapter found")
    check("start() False when Bluetooth fails", not await c2.start())
    check("error kind no_adapter", ("error", {"kind": "no_adapter", "message": "No Bluetooth adapter found"}) in ev2)
    FakeServer.fail = None
    check("start() works again after a failure (Retry)", await c2.start())
    await c2.shutdown()

    class Boom(sys.modules["evdev"].UInput.__class__): pass
    import injector_linux
    orig = injector_linux.Injector
    def deny(*a, **k): raise PermissionError("/dev/uinput")
    injector_linux.Injector = deny
    ev3 = []; c3 = C.BridgeCore(lambda n, **d: ev3.append((n, d)))
    ok = await c3.start(); injector_linux.Injector = orig
    check("no uinput permission -> error no_permission", not ok and ev3[0][1]["kind"] == "no_permission")

    print("\nALL PASSED" if not fails else f"\nFAILED: {fails}")
    return not fails

sys.exit(0 if asyncio.run(main()) else 1)
