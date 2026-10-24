"""
Shared protocol constants and signing helpers.

Nothing in here touches Bluetooth or the keyboard, so a future Windows receiver
can reuse this file unchanged.

Message format (both directions): [type: 1][seq: 1][payload][tag: 4 bytes]
The tag is an HMAC signature, present on every message except the handshake ones.
"""

import hashlib
import hmac

DEVICE_NAME = "PCBridge"
SERVICE_UUID = "b5a3f001-7c1e-4b8a-9d2f-3e6a1c4d8f10"
RX_UUID = "b5a3f002-7c1e-4b8a-9d2f-3e6a1c4d8f10"  # phone -> PC (write)
TX_UUID = "b5a3f003-7c1e-4b8a-9d2f-3e6a1c4d8f10"  # PC -> phone (notify)

# Phone -> PC
T_HEARTBEAT = 0x01
T_PING = 0x02
T_HELLO = 0x04
T_AUTH = 0x05
T_TEXT = 0x10            # also PC -> phone
T_KEY_DOWN = 0x20        # [usage]  key pressed and held
T_KEY_UP = 0x21          # [usage]  key released
T_RELEASE_ALL = 0x22     # release every key and button
T_KEY_TAP = 0x23         # [usage]  press and release in one go
T_MOUSE_MOVE = 0x30      # [dx int16 LE][dy int16 LE]
T_MOUSE_BTN = 0x31       # [button 1/2/3][action 0 up / 1 down / 2 click]
T_MOUSE_WHEEL = 0x32     # [ticks int8], positive = scroll up
T_GAMEPAD_STATE = 0x40   # [buttons: 3 bytes][LX][LY][RX][RY], axes int8, down/right positive

# PC -> phone
T_ACK = 0x81
T_PONG = 0x82
T_CHALLENGE = 0x83
T_AUTH_OK = 0x84
T_AUTH_FAIL = 0x85

NONCE_LEN = 8
PROOF_LEN = 8
TAG_LEN = 4
MAX_PAYLOAD = 20 - 2 - TAG_LEN   # 20-byte default BLE limit minus header and tag
PHONE_TIMEOUT = 3.0              # seconds of silence before the phone counts as gone
MAX_TRIES = 3
LOCKOUT_SECONDS = 60

# Gamepad button bit order. Bit 0 = "A", bit 1 = "B", ... Must match BITS in gamepad.js.
PAD_BITS = ["A", "B", "X", "Y", "L1", "R1", "L2", "R2", "Select", "Start",
            "L3", "R3", "Up", "Down", "Left", "Right", "Home"]


def mac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def make_tag(key: bytes, direction: bytes, msg_type: int, seq: int, payload: bytes) -> bytes:
    return mac(key, direction + bytes([msg_type, seq]) + payload)[:TAG_LEN]
