"""
Linux injector: creates a virtual keyboard, mouse and gamepad with uinput and
turns the phone's messages into real input events.

Every input method (key_down, key_up, ...) does nothing while `enabled` is False.
release_all() always works, so nothing can be left stuck down.

dry_run=True creates no devices (for testing without permissions): it only tracks state.
A future Windows receiver would provide a class with the same method names.
"""

import time

from evdev import AbsInfo, UInput, ecodes as e

from keymap_linux import HID_TO_KEY, usage_name
from protocol import PAD_BITS

MOUSE_BUTTONS = {1: e.BTN_LEFT, 2: e.BTN_RIGHT, 3: e.BTN_MIDDLE}
MOUSE_NAMES = {1: "left", 2: "right", 3: "middle"}

# Phone layout: Y top, X left, B right, A bottom (same as an Xbox pad).
PAD_BUTTONS = {
    "A": e.BTN_SOUTH, "B": e.BTN_EAST, "X": e.BTN_WEST, "Y": e.BTN_NORTH,
    "L1": e.BTN_TL, "R1": e.BTN_TR, "L2": e.BTN_TL2, "R2": e.BTN_TR2,
    "Select": e.BTN_SELECT, "Start": e.BTN_START,
    "L3": e.BTN_THUMBL, "R3": e.BTN_THUMBR, "Home": e.BTN_MODE,
}
_PAD_MASK_ALL = (1 << len(PAD_BITS)) - 1
_BIT = {name: i for i, name in enumerate(PAD_BITS)}
STICK_AXES = [e.ABS_X, e.ABS_Y, e.ABS_RX, e.ABS_RY]


class Injector:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.enabled = True
        self.keys_down = set()      # HID usages currently held
        self.mouse_down = set()     # mouse button numbers currently held
        self.pad_mask = 0
        self.pad_axes = [0, 0, 0, 0]
        self.pad_hat = (0, 0)
        self.kb = self.mouse = self.pad = None

        if not dry_run:
            stick = AbsInfo(value=0, min=-32768, max=32767, fuzz=0, flat=0, resolution=0)
            hat = AbsInfo(value=0, min=-1, max=1, fuzz=0, flat=0, resolution=0)
            self.kb = UInput({e.EV_KEY: sorted(set(HID_TO_KEY.values()))}, name="PCBridge Keyboard")
            self.mouse = UInput(
                {e.EV_KEY: list(MOUSE_BUTTONS.values()), e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL]},
                name="PCBridge Mouse")
            self.pad = UInput(
                {e.EV_KEY: list(PAD_BUTTONS.values()),
                 e.EV_ABS: [(a, stick) for a in STICK_AXES] + [(e.ABS_HAT0X, hat), (e.ABS_HAT0Y, hat)]},
                name="PCBridge Gamepad")
            time.sleep(0.3)   # give the desktop a moment to notice the new devices

    # ---------- low level ----------

    @staticmethod
    def _put(dev, etype, code, value):
        if dev is not None:
            dev.write(etype, code, value)

    @staticmethod
    def _sync(dev):
        if dev is not None:
            dev.syn()

    # ---------- control ----------

    def set_enabled(self, on: bool):
        """Emergency stop: False releases everything and ignores input until turned back on."""
        self.enabled = on
        if not on:
            self.release_all()

    # ---------- keyboard ----------

    def key_down(self, usage: int):
        code = HID_TO_KEY.get(usage)
        if code is None or not self.enabled:
            return
        self.keys_down.add(usage)
        self._put(self.kb, e.EV_KEY, code, 1)
        self._sync(self.kb)

    def key_up(self, usage: int):
        code = HID_TO_KEY.get(usage)
        if code is None or usage not in self.keys_down:   # never "release" a key we did not press
            return
        self.keys_down.discard(usage)
        self._put(self.kb, e.EV_KEY, code, 0)
        self._sync(self.kb)

    def key_tap(self, usage: int):
        code = HID_TO_KEY.get(usage)
        if code is None or not self.enabled:
            return
        self._put(self.kb, e.EV_KEY, code, 1)
        self._sync(self.kb)
        self._put(self.kb, e.EV_KEY, code, 0)
        self._sync(self.kb)

    # ---------- mouse ----------

    def mouse_move(self, dx: int, dy: int):
        if not self.enabled or not (dx or dy):
            return
        if dx:
            self._put(self.mouse, e.EV_REL, e.REL_X, dx)
        if dy:
            self._put(self.mouse, e.EV_REL, e.REL_Y, dy)
        self._sync(self.mouse)

    def mouse_button(self, button: int, action: int):
        """button 1 left, 2 right, 3 middle; action 0 up, 1 down, 2 click."""
        code = MOUSE_BUTTONS.get(button)
        if code is None or not self.enabled:
            return
        if action == 2:
            self._put(self.mouse, e.EV_KEY, code, 1)
            self._sync(self.mouse)
            self._put(self.mouse, e.EV_KEY, code, 0)
            self._sync(self.mouse)
        elif action == 1:
            self.mouse_down.add(button)
            self._put(self.mouse, e.EV_KEY, code, 1)
            self._sync(self.mouse)
        elif action == 0 and button in self.mouse_down:
            self.mouse_down.discard(button)
            self._put(self.mouse, e.EV_KEY, code, 0)
            self._sync(self.mouse)

    def mouse_wheel(self, ticks: int):
        if self.enabled and ticks:
            self._put(self.mouse, e.EV_REL, e.REL_WHEEL, ticks)
            self._sync(self.mouse)

    # ---------- gamepad ----------

    def gamepad(self, mask: int, axes):
        """mask = button bits (see PAD_BITS); axes = [LX, LY, RX, RY] as int8 -127..127."""
        if not self.enabled:
            return
        mask &= _PAD_MASK_ALL
        wrote = False

        diff = mask ^ self.pad_mask
        for name, code in PAD_BUTTONS.items():
            if diff >> _BIT[name] & 1:
                self._put(self.pad, e.EV_KEY, code, mask >> _BIT[name] & 1)
                wrote = True

        hat = ((mask >> _BIT["Right"] & 1) - (mask >> _BIT["Left"] & 1),
               (mask >> _BIT["Down"] & 1) - (mask >> _BIT["Up"] & 1))
        if hat != self.pad_hat:
            self._put(self.pad, e.EV_ABS, e.ABS_HAT0X, hat[0])
            self._put(self.pad, e.EV_ABS, e.ABS_HAT0Y, hat[1])
            self.pad_hat = hat
            wrote = True

        for i, code in enumerate(STICK_AXES):
            v = max(-127, min(127, int(axes[i]))) * 32767 // 127
            if v != self.pad_axes[i]:
                self._put(self.pad, e.EV_ABS, code, v)
                self.pad_axes[i] = v
                wrote = True

        self.pad_mask = mask
        if wrote:
            self._sync(self.pad)

    # ---------- safety ----------

    def release_all(self):
        """Release every key, mouse button and pad button. Returns what was held (for the log)."""
        held = sorted(usage_name(u) for u in self.keys_down)
        held += ["mouse:" + MOUSE_NAMES[b] for b in sorted(self.mouse_down)]
        held += ["pad:" + n for i, n in enumerate(PAD_BITS) if self.pad_mask >> i & 1]

        for usage in list(self.keys_down):
            self._put(self.kb, e.EV_KEY, HID_TO_KEY[usage], 0)
        self._sync(self.kb)
        self.keys_down.clear()

        for button in list(self.mouse_down):
            self._put(self.mouse, e.EV_KEY, MOUSE_BUTTONS[button], 0)
        self._sync(self.mouse)
        self.mouse_down.clear()

        for name, code in PAD_BUTTONS.items():
            if self.pad_mask >> _BIT[name] & 1:
                self._put(self.pad, e.EV_KEY, code, 0)
        for code in STICK_AXES:
            self._put(self.pad, e.EV_ABS, code, 0)
        self._put(self.pad, e.EV_ABS, e.ABS_HAT0X, 0)
        self._put(self.pad, e.EV_ABS, e.ABS_HAT0Y, 0)
        self._sync(self.pad)
        self.pad_mask = 0
        self.pad_axes = [0, 0, 0, 0]
        self.pad_hat = (0, 0)
        return held

    def close(self):
        try:
            self.release_all()
        finally:
            for dev in (self.kb, self.mouse, self.pad):
                if dev is not None:
                    dev.close()
