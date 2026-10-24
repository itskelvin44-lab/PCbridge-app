"""Fake evdev and fake bless, so the core can run without Bluetooth or /dev/uinput."""
import asyncio, os, secrets, sys, types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ---------- fake evdev ----------
EVENTS = {}          # device name -> list of (type, code, value)
class _Codes(types.ModuleType):
    _n = {}
    def __getattr__(self, name):
        if name.startswith("__"): raise AttributeError(name)
        return self._n.setdefault(name, len(self._n) + 1)
ec = _Codes("evdev.ecodes")
class FakeUInput:
    def __init__(self, caps, name=""):
        self.name = name; EVENTS[name] = []
    def write(self, t, c, v): EVENTS[self.name].append((t, c, v))
    def syn(self): pass
    def close(self): pass
ev = types.ModuleType("evdev")
ev.UInput = FakeUInput; ev.AbsInfo = lambda **k: k; ev.ecodes = ec
sys.modules["evdev"] = ev; sys.modules["evdev.ecodes"] = ec

# ---------- fake bless ----------
class FakeChar:
    value = None
class FakeServer:
    last = None
    fail = None
    def __init__(self, name=None, loop=None):
        self.chars = {}; self.write_request_func = None; self.sent = []; FakeServer.last = self
    async def add_new_service(self, u): pass
    async def add_new_characteristic(self, s, u, *a): self.chars[u] = FakeChar()
    async def start(self):
        if FakeServer.fail: raise FakeServer.fail
    async def stop(self): pass
    def get_characteristic(self, u): return self.chars[u]
    def update_value(self, s, u): self.sent.append(bytes(self.chars[u].value))
bl = types.ModuleType("bless")
bl.BlessServer = FakeServer
bl.GATTCharacteristicProperties = types.SimpleNamespace(write=1, write_without_response=2, read=4, notify=8)
bl.GATTAttributePermissions = types.SimpleNamespace(writeable=1, readable=2)
sys.modules["bless"] = bl

