"""
Terminal front end for the bridge (for development and for seeing raw errors).
The window front end is gui.py. Both use the same BridgeCore.

  python ble_peripheral.py              real injection
  python ble_peripheral.py --print-only no devices created, only prints (safe testing)
  python ble_peripheral.py --quiet      do not print every key / mouse / pad event

Terminal commands (type + Enter):
  /stop    emergency stop: release everything and ignore the phone's input
  /go      start injecting again
  /status  show state
  anything else is sent to the phone as text (max 14 bytes)
"""

import argparse
import asyncio
import sys
import threading

from core import BridgeCore

HELP = {
    "no_permission": "Cannot open /dev/uinput. Follow the one-time setup (uinput permissions), "
                     "or run with --print-only.",
    "bluetooth_off": "Bluetooth seems to be off. Turn it on and run again.",
    "no_adapter": "No Bluetooth adapter found.",
}


async def run(print_only: bool, quiet: bool):
    core = BridgeCore(emit=lambda *a, **k: None, dry_run=print_only, verbose=not quiet)

    def on_event(name, **d):
        if name == "code":
            print(f"\n*** Session code: {d['value']} ***  (type this on the phone)\n")
        elif name in ("log", "input"):
            print(d["text"])
        elif name == "error":
            print(f"\n[error] {d['kind']}: {d['message']}")
            if d["kind"] in HELP:
                print(HELP[d["kind"]])
        elif name == "state":
            print(f"[state] {d['value']}")

    core._emit_cb = on_event

    if print_only:
        print("*** PRINT-ONLY mode: nothing will be typed or clicked on this laptop ***")
    if not await core.start():
        sys.exit(1)
    if not print_only:
        print("*** Real injection is ON. Type /stop + Enter here to stop it instantly. ***")
    print("Waiting for a phone. Type text + Enter to send to it. Ctrl+C to quit.\n")

    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()

    def reader():
        while True:
            try:
                line = sys.stdin.readline()
            except Exception:
                break
            if not line:
                break
            loop.call_soon_threadsafe(queue.put_nowait, line.rstrip("\n"))

    threading.Thread(target=reader, daemon=True).start()

    try:
        while True:
            line = await queue.get()
            if not line:
                continue
            cmd = line.strip().lower()
            if cmd == "/stop":
                core._set_injection(False)
            elif cmd == "/go":
                core._set_injection(True)
            elif cmd == "/status":
                mode = "print-only" if core.dry_run else "real"
                print(f"[status] injection {'ON' if core.injector.enabled else 'OFF'} ({mode}), "
                      f"phone {'logged in' if core.phone_present else 'not connected'}")
            elif line.startswith("/"):
                print("[note] commands: /stop  /go  /status")
            else:
                core._send_text(line)
    finally:
        await core.shutdown()


def main():
    ap = argparse.ArgumentParser(description="PC Bridge laptop receiver (terminal)")
    ap.add_argument("--print-only", action="store_true", help="do not inject, only print")
    ap.add_argument("--quiet", action="store_true", help="do not print every key/mouse/pad event")
    args = ap.parse_args()
    try:
        asyncio.run(run(args.print_only, args.quiet))
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
