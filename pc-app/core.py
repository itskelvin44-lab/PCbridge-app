"""
BridgeCore: Bluetooth server, 4-digit login, signatures, heartbeat watchdog and input
injection. It never prints and never reads the keyboard. It REPORTS EVENTS to whoever
created it (the window or the terminal) and accepts a few commands.

Commands (call from any thread):   start() / shutdown() are coroutines, run them on the
                                   core's asyncio loop. set_injection(bool), send_text(str).

Events, delivered as emit(name, **data). Only sent when something changes, never per mouse move:
  code       value="1234"                     a new session code exists
  state      value="waiting|connecting|connected|lost"
  injection  value=True/False                 emergency stop toggled
  auth       kind="wrong_code", value=tries_left     or   kind="locked", value=seconds
  held       value=[names]                    what release_all() released
  log        text="..."                       a line for the Details panel (never contains typed keys)
  input      text="..."                       one line per key / mouse / pad event (only if verbose)
  error      kind="no_permission|bluetooth_off|no_adapter|already_running|other", message="..."

EXPERIMENT-GRADE SECURITY (unchanged): the code has 10,000 possibilities and nothing is
encrypted, only signed. See my_progress_4 section 7.

Handshake (code = the 4 digits, typed on the phone):
  phone -> PC  0x04 HELLO      payload: phone nonce (8 bytes)
  PC -> phone  0x83 CHALLENGE  payload: PC nonce (8 bytes)
  phone -> PC  0x05 AUTH       payload: HMAC(code, "P"+np+nl)[:8]
  PC -> phone  0x84 AUTH_OK    payload: HMAC(code, "L"+np+nl)[:8]
           or  0x85 AUTH_FAIL  payload: [reason, value]  0 = wrong code (tries left), 1 = locked (seconds)
  session key = HMAC(code, "K"+np+nl).
"""

import asyncio
import hmac
import secrets
import time

from keymap_linux import usage_name
from protocol import (
    DEVICE_NAME, SERVICE_UUID, RX_UUID, TX_UUID,
    T_HEARTBEAT, T_PING, T_HELLO, T_AUTH, T_TEXT,
    T_KEY_DOWN, T_KEY_UP, T_RELEASE_ALL, T_KEY_TAP,
    T_MOUSE_MOVE, T_MOUSE_BTN, T_MOUSE_WHEEL, T_GAMEPAD_STATE,
    T_ACK, T_PONG, T_CHALLENGE, T_AUTH_OK, T_AUTH_FAIL,
    NONCE_LEN, PROOF_LEN, TAG_LEN, MAX_PAYLOAD, PHONE_TIMEOUT, MAX_TRIES, LOCKOUT_SECONDS,
    PAD_BITS, mac, make_tag,
)


def classify_error(exc: Exception) -> str:
    """Best guess at what went wrong when Bluetooth cannot start. The wording of the
    underlying errors is NOT confirmed on a real machine, so anything unknown is 'other'
    and the window shows the original message."""
    s = str(exc).lower()
    if "adapter" in s and ("no " in s or "not found" in s or "not available" in s):
        return "no_adapter"
    if any(w in s for w in ("not powered", "powered off", "rfkill", "blocked", "bluetooth is off")):
        return "bluetooth_off"
    return "other"


class BridgeCore:
    def __init__(self, emit, dry_run: bool = False, verbose: bool = False):
        self._emit_cb = emit
        self.dry_run = dry_run
        self.verbose = verbose            # may be changed at any time from any thread

        self.loop = None
        self.server = None
        self.injector = None
        self._watchdog_task = None
        self.running = False

        self.state = None
        self.tx_seq = 0
        self.phone_present = False
        self.last_heard = 0.0
        self.expected_seq = None
        self.last_seq = None

        self.code = ""
        self.session_key = None
        self.pending = None
        self.failures = 0
        self.locked_until = 0.0
        self.last_reject_print = 0.0

        self.pad_last = 0.0
        self.mouse_acc = [0, 0, 0.0]      # batched mouse movement, only for the verbose lines

    # ------------------------------------------------------------------ events

    def emit(self, name: str, **data):
        try:
            self._emit_cb(name, **data)
        except Exception:                 # a broken front end must never kill the link
            pass

    def log(self, text: str):
        self.emit("log", text=text)

    def _say(self, text: str):
        if self.verbose:
            self.emit("input", text=text)

    def _set_state(self, value: str):
        if value != self.state:
            self.state = value
            self.emit("state", value=value)

    def _new_code(self):
        self.code = f"{secrets.randbelow(10000):04d}"
        self.emit("code", value=self.code)

    # ------------------------------------------------------------------ commands

    async def start(self) -> bool:
        """Create the virtual devices, start advertising, show a code. False = failed (an
        'error' event says why and everything is cleaned up, so start() may be called again)."""
        self.loop = asyncio.get_running_loop()

        try:
            from injector_linux import Injector
            self.injector = Injector(dry_run=True) if self.dry_run else Injector()
        except (PermissionError, FileNotFoundError) as exc:
            self.emit("error", kind="no_permission", message=str(exc))
            return False
        except Exception as exc:
            self.emit("error", kind="other", message=f"input devices: {exc!r}")
            return False

        try:
            from bless import (BlessServer, GATTCharacteristicProperties,
                               GATTAttributePermissions)
            self.server = BlessServer(name=DEVICE_NAME, loop=self.loop)
            self.server.write_request_func = self._on_write
            await self.server.add_new_service(SERVICE_UUID)
            await self.server.add_new_characteristic(
                SERVICE_UUID, RX_UUID,
                GATTCharacteristicProperties.write | GATTCharacteristicProperties.write_without_response,
                None, GATTAttributePermissions.writeable)
            await self.server.add_new_characteristic(
                SERVICE_UUID, TX_UUID,
                GATTCharacteristicProperties.read | GATTCharacteristicProperties.notify,
                bytearray(b""), GATTAttributePermissions.readable)
            await self.server.start()
        except Exception as exc:
            self.emit("error", kind=classify_error(exc), message=str(exc) or repr(exc))
            await self._cleanup()
            return False

        self.running = True
        self._set_state("waiting")
        self.emit("injection", value=self.injector.enabled)
        self.log(f"Advertising as '{DEVICE_NAME}'" + (" (print-only: nothing is typed)" if self.dry_run else ""))
        self._new_code()
        self._watchdog_task = asyncio.create_task(self._watchdog())
        return True

    async def shutdown(self):
        """Release everything, remove the virtual devices, stop advertising. Safe to call twice."""
        self.running = False
        if self._watchdog_task:
            self._watchdog_task.cancel()
            self._watchdog_task = None
        await self._cleanup()

    async def _cleanup(self):
        self.session_key = None
        self.phone_present = False
        if self.injector is not None:
            try:
                self.injector.close()
            except Exception as exc:
                self.log(f"[error] closing input devices: {exc!r}")
            self.injector = None
        if self.server is not None:
            try:
                await self.server.stop()
            except Exception:
                pass
            self.server = None

    def set_injection(self, on: bool):
        if self.loop:
            self.loop.call_soon_threadsafe(self._set_injection, on)

    def _set_injection(self, on: bool):
        if self.injector is None:
            return
        self.injector.set_enabled(on)
        if not on:
            self.emit("held", value=[])
        self.emit("injection", value=on)
        self.log("[safety] injection ON" if on else "[safety] INJECTION STOPPED")

    def send_text(self, text: str):
        if self.loop:
            self.loop.call_soon_threadsafe(self._send_text, text)

    def _send_text(self, text: str):
        payload = text.encode()
        if len(payload) > MAX_PAYLOAD:
            self.log(f"[note] message is {len(payload)} bytes, cutting to {MAX_PAYLOAD}")
            payload = payload[:MAX_PAYLOAD]
        self._send_to_phone(T_TEXT, self.tx_seq, payload)
        self.tx_seq = (self.tx_seq + 1) & 0xFF

    # ------------------------------------------------------------------ safety

    def release_all(self):
        held = self.injector.release_all() if self.injector else []
        self.emit("held", value=held)
        self.log(f"[safety] release_all() called (held: {held or 'nothing'})")

    def _end_session(self):
        was_present = self.phone_present
        self.phone_present = False
        self.expected_seq = None
        self.last_seq = None
        self.session_key = None
        self.pending = None
        self.release_all()     # nothing may stay held when a session ends
        self._set_state("lost" if was_present else "waiting")

    async def _watchdog(self):
        """Declare the phone gone if its (signed) heartbeat stops."""
        while True:
            await asyncio.sleep(0.5)
            self._flush_mouse()
            if self.phone_present and time.monotonic() - self.last_heard > PHONE_TIMEOUT:
                self.log(f"[link] phone silent for {PHONE_TIMEOUT:.0f}s, treating as disconnected")
                self._end_session()

    # ------------------------------------------------------------------ sending

    def _send_raw(self, msg_type: int, seq: int, body: bytes):
        """Send exactly as given (the unsigned handshake messages)."""
        char = self.server.get_characteristic(TX_UUID)
        char.value = bytearray(bytes([msg_type, seq & 0xFF]) + body)
        self.server.update_value(SERVICE_UUID, TX_UUID)

    def _send_to_phone(self, msg_type: int, seq: int, payload: bytes = b"", quiet_send: bool = False):
        if self.session_key is None:
            self.log("[note] no logged-in phone, not sending")
            return
        tag = make_tag(self.session_key, b"L", msg_type, seq & 0xFF, payload)
        self._send_raw(msg_type, seq, payload + tag)
        if not quiet_send:
            self.log(f"[sent] type=0x{msg_type:02x} seq={seq}")

    # ------------------------------------------------------------------ incoming frames

    def _on_write(self, characteristic, value, **kwargs):
        characteristic.value = value
        data = bytes(value)
        if len(data) < 2:
            self.log(f"[bad frame] too short: {data!r}")
            return
        msg_type, seq, rest = data[0], data[1], data[2:]
        if msg_type == T_HELLO:
            self._handle_hello(seq, rest)
        elif msg_type == T_AUTH:
            self._handle_auth(seq, rest)
        else:
            self._handle_signed(msg_type, seq, rest)

    def _handle_hello(self, seq: int, body: bytes):
        now = time.monotonic()
        if now < self.locked_until:
            self._send_raw(T_AUTH_FAIL, seq, bytes([1, min(255, int(self.locked_until - now) + 1)]))
            return
        if len(body) != NONCE_LEN:
            return
        self.pending = (body, secrets.token_bytes(NONCE_LEN))
        if not self.phone_present:
            self._set_state("connecting")
        self._send_raw(T_CHALLENGE, seq, self.pending[1])

    def _handle_auth(self, seq: int, body: bytes):
        now = time.monotonic()
        if self.pending is None:
            return
        if now < self.locked_until:
            self.pending = None
            self._send_raw(T_AUTH_FAIL, seq, bytes([1, min(255, int(self.locked_until - now) + 1)]))
            return

        np_, nl = self.pending
        self.pending = None
        expected = mac(self.code.encode(), b"P" + np_ + nl)[:PROOF_LEN]

        if hmac.compare_digest(expected, body):
            self.failures = 0
            self.session_key = mac(self.code.encode(), b"K" + np_ + nl)
            self.phone_present = True
            self.last_heard = now
            self.last_seq = seq
            self.expected_seq = (seq + 1) & 0xFF
            self.release_all()   # a fresh login starts with everything released
            self.log("[link] phone logged in")
            self._set_state("connected")
            self._send_raw(T_AUTH_OK, seq, mac(self.code.encode(), b"L" + np_ + nl)[:PROOF_LEN])
            return

        self.failures += 1
        self.log(f"[auth] wrong code ({self.failures}/{MAX_TRIES})")
        if self.failures >= MAX_TRIES:
            self.failures = 0
            self.locked_until = now + LOCKOUT_SECONDS
            self._end_session()
            self.log(f"[auth] locked for {LOCKOUT_SECONDS}s, making a new code")
            self._new_code()
            self.emit("auth", kind="locked", value=LOCKOUT_SECONDS)
            self._send_raw(T_AUTH_FAIL, seq, bytes([1, LOCKOUT_SECONDS]))
        else:
            left = MAX_TRIES - self.failures
            if not self.phone_present:
                self._set_state("waiting")
            self.emit("auth", kind="wrong_code", value=left)
            self._send_raw(T_AUTH_FAIL, seq, bytes([0, left]))

    def _handle_signed(self, msg_type: int, seq: int, rest: bytes):
        def reject(why: str):
            now = time.monotonic()
            if now - self.last_reject_print > 5:
                self.last_reject_print = now
                self.log(f"[reject] {why}")

        if self.session_key is None:
            reject("message before login, ignored")
            return
        if len(rest) < TAG_LEN:
            reject("too short")
            return
        payload, tag = rest[:-TAG_LEN], rest[-TAG_LEN:]
        if not hmac.compare_digest(tag, make_tag(self.session_key, b"P", msg_type, seq, payload)):
            reject("bad signature")
            return
        diff = (seq - self.last_seq) & 0xFF
        if diff == 0 or diff >= 128:
            reject(f"replayed or old sequence number {seq}")
            return
        self.last_seq = seq
        self.last_heard = time.monotonic()

        if self.expected_seq is not None and seq != self.expected_seq:
            missed = (seq - self.expected_seq) & 0xFF
            self.log(f"[warn] sequence jump: expected {self.expected_seq}, got {seq} "
                     f"({missed} missing or out of order)")
        self.expected_seq = (seq + 1) & 0xFF

        if msg_type == T_HEARTBEAT:
            self._send_to_phone(T_ACK, seq, quiet_send=True)
        elif msg_type == T_PING:
            self.log(f"[received] PING seq={seq}")
            self._send_to_phone(T_PONG, seq)
        elif msg_type == T_TEXT:
            self.log(f"[received] TEXT seq={seq} {payload.decode(errors='replace')!r}")
        else:
            try:
                handled = self._handle_input(msg_type, payload)
            except Exception as exc:   # an injection error must never kill the link
                self.log(f"[error] injecting type=0x{msg_type:02x} failed: {exc!r}")
                return
            if not handled:
                self.log(f"[received] unknown type=0x{msg_type:02x} seq={seq}")

    # ------------------------------------------------------------------ input

    def _flush_mouse(self):
        if self.mouse_acc[0] or self.mouse_acc[1]:
            self._say(f"[mouse] move dx={self.mouse_acc[0]} dy={self.mouse_acc[1]}")
            self.mouse_acc[0] = self.mouse_acc[1] = 0

    @staticmethod
    def _pad_names(mask: int):
        return [n for i, n in enumerate(PAD_BITS) if mask >> i & 1]

    def _handle_input(self, t: int, p: bytes) -> bool:
        """Inject an input event. Returns False if the type is not an input message."""
        inj = self.injector
        if inj is None:
            return True
        if t in (T_KEY_DOWN, T_KEY_UP, T_KEY_TAP) and len(p) == 1:
            if t == T_KEY_DOWN:
                inj.key_down(p[0])
            elif t == T_KEY_UP:
                inj.key_up(p[0])
            else:
                inj.key_tap(p[0])
            kind = {T_KEY_DOWN: "down", T_KEY_UP: "up", T_KEY_TAP: "tap"}[t]
            self._say(f"[key] {kind:4} {usage_name(p[0])}")
        elif t == T_RELEASE_ALL:
            self.release_all()
        elif t == T_MOUSE_MOVE and len(p) == 4:
            dx = int.from_bytes(p[0:2], "little", signed=True)
            dy = int.from_bytes(p[2:4], "little", signed=True)
            inj.mouse_move(dx, dy)
            self.mouse_acc[0] += dx
            self.mouse_acc[1] += dy
            if time.monotonic() - self.mouse_acc[2] > 0.2:
                self.mouse_acc[2] = time.monotonic()
                self._flush_mouse()
        elif t == T_MOUSE_BTN and len(p) == 2:
            self._flush_mouse()
            inj.mouse_button(p[0], p[1])
            btn = {1: "left", 2: "right", 3: "middle"}.get(p[0], p[0])
            act = {0: "up", 1: "down", 2: "click"}.get(p[1], p[1])
            self._say(f"[mouse] {btn} {act}")
        elif t == T_MOUSE_WHEEL and len(p) == 1:
            self._flush_mouse()
            ticks = int.from_bytes(p, "little", signed=True)
            inj.mouse_wheel(ticks)
            self._say(f"[mouse] wheel {ticks:+d}")
        elif t == T_GAMEPAD_STATE and len(p) == 7:
            mask = p[0] | p[1] << 8 | p[2] << 16
            raw = [int.from_bytes(p[i:i + 1], "little", signed=True) for i in range(3, 7)]
            was, now = inj.pad_mask, time.monotonic()
            inj.gamepad(mask, raw)
            ax = [v / 127 for v in raw]
            neutral = mask == 0 and not any(raw)
            if mask != was or neutral or (any(raw) and now - self.pad_last > 0.25):
                self.pad_last = now
                self._say(f"[pad] {'+'.join(self._pad_names(mask)) or '-':<12} "
                          f"LS({ax[0]:+.2f},{ax[1]:+.2f}) RS({ax[2]:+.2f},{ax[3]:+.2f})")
        else:
            return False
        return True
