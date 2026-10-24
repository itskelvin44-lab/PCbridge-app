"""
Lookup table: USB HID usage ID (what the phone sends) -> Linux key code (what uinput needs).

Example: 0x04 (HID "a") -> KEY_A, 0xE1 (HID left Shift) -> KEY_LEFTSHIFT.
Linux Print Screen is KEY_SYSRQ, which is what a real Print Screen key sends.
"""

from evdev import ecodes as e

_NAMES = {}   # usage -> "KEY_xxx"

for _i, _ch in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
    _NAMES[0x04 + _i] = "KEY_" + _ch
for _i, _ch in enumerate("123456789"):
    _NAMES[0x1E + _i] = "KEY_" + _ch
for _i in range(12):
    _NAMES[0x3A + _i] = "KEY_F%d" % (_i + 1)

_NAMES.update({
    0x27: "KEY_0",
    0x28: "KEY_ENTER", 0x29: "KEY_ESC", 0x2A: "KEY_BACKSPACE", 0x2B: "KEY_TAB", 0x2C: "KEY_SPACE",
    0x2D: "KEY_MINUS", 0x2E: "KEY_EQUAL", 0x2F: "KEY_LEFTBRACE", 0x30: "KEY_RIGHTBRACE",
    0x31: "KEY_BACKSLASH", 0x33: "KEY_SEMICOLON", 0x34: "KEY_APOSTROPHE", 0x35: "KEY_GRAVE",
    0x36: "KEY_COMMA", 0x37: "KEY_DOT", 0x38: "KEY_SLASH", 0x39: "KEY_CAPSLOCK",
    0x46: "KEY_SYSRQ",
    # not on the phone keyboard yet, ready for a future Fn layer
    0x49: "KEY_INSERT", 0x4A: "KEY_HOME", 0x4B: "KEY_PAGEUP", 0x4C: "KEY_DELETE",
    0x4D: "KEY_END", 0x4E: "KEY_PAGEDOWN",
    0x4F: "KEY_RIGHT", 0x50: "KEY_LEFT", 0x51: "KEY_DOWN", 0x52: "KEY_UP",
    # modifiers
    0xE0: "KEY_LEFTCTRL", 0xE1: "KEY_LEFTSHIFT", 0xE2: "KEY_LEFTALT", 0xE3: "KEY_LEFTMETA",
    0xE4: "KEY_RIGHTCTRL", 0xE5: "KEY_RIGHTSHIFT", 0xE6: "KEY_RIGHTALT", 0xE7: "KEY_RIGHTMETA",
})

HID_TO_KEY = {usage: getattr(e, name) for usage, name in _NAMES.items()}


def usage_name(usage: int) -> str:
    """Readable name for logging, e.g. 0x33 -> 'semicolon'."""
    n = _NAMES.get(usage)
    return n[4:].lower() if n else "0x%02x" % usage
