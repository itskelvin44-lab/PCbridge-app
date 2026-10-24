"""Drives the real window (needs a display; use xvfb-run) with fake Bluetooth and devices.
  xvfb-run -a python tests/test_gui.py"""
import os, sys, time, tkinter as tk
sys.path.insert(0, os.path.dirname(__file__))
from fakes import *
import shutil; shutil.rmtree("/tmp/pcb_test", ignore_errors=True)
os.environ["XDG_CONFIG_HOME"] = os.environ["XDG_STATE_HOME"] = os.environ["XDG_CACHE_HOME"] = "/tmp/pcb_test"
import gui
from protocol import *

fails = []
def check(n, c):
    print(("PASS " if c else "FAIL ") + n)
    if not c: fails.append(n)

def pump(root, secs):
    end = time.time() + secs
    while time.time() < end:
        root.update(); time.sleep(0.02)

check("first instance gets the lock", gui.acquire_single_instance())
import subprocess
r = subprocess.run([sys.executable, "-c", "import sys;sys.path[:0]=['.','tests'];import fakes;import gui;print(gui.acquire_single_instance())"],
                   capture_output=True, text=True, env=os.environ)
check("second instance is refused", r.stdout.strip() == "False")

root = tk.Tk(); app = gui.App(root); pump(root, 0.6)
srv = FakeServer.last
check("code shown as 4 digits with spaces", len(app.code_var.get().split()) == 4)
check("status waiting", app.status_var.get().startswith("Waiting"))
code = app.core.code

class Ph:
    def __init__(s): s.seq = 0
    def w(s, d): srv.write_request_func(srv.chars[RX_UUID], bytearray(d))
    def login(s, code):
        np_ = secrets.token_bytes(8); s.w(bytes([T_HELLO, 1]) + np_)
        nl = srv.sent[-1][2:10]; s.w(bytes([T_AUTH, 2]) + mac(code.encode(), b"P" + np_ + nl)[:8])
        s.key = mac(code.encode(), b"K" + np_ + nl); s.seq = 2
    def send(s, t, p=b""):
        s.seq += 1; s.w(bytes([t, s.seq]) + p + make_tag(s.key, b"P", t, s.seq, p))

ph = Ph(); bad = "0000" if code != "0000" else "1111"
ph.login(bad); pump(root, 0.3)
check("wrong code message", "tries left" in app.auth_var.get())
ph.login(code); pump(root, 0.3)
check("status Phone connected", app.status_var.get() == "Phone connected")

app.toggle_injection(); pump(root, 0.3)
check("STOP -> button says START, Input: STOPPED", app.stop_btn.cget("text") == "START" and "STOPPED" in app.inj_var.get())
app.toggle_injection(); pump(root, 0.3)
check("START -> back to STOP / ON", app.stop_btn.cget("text") == "STOP" and app.inj_var.get() == "Input: ON")

# typed keys are hidden by default
ph.send(T_KEY_TAP, bytes([0x04])); pump(root, 0.3)
check("typed keys NOT in Details by default", "[key]" not in app.text.get("1.0", "end"))
app.keys_var.set(True); app._keys_changed(); ph.send(T_KEY_TAP, bytes([0x04])); pump(root, 0.3)
check("typed keys shown when the setting is on", "[key]" in app.text.get("1.0", "end"))
logfile = open("/tmp/pcb_test/pcbridge/pcbridge.log").read()
check("log file has login line, no key lines, no code", "phone logged in" in logfile and "[key]" not in logfile and code not in logfile)

app.show_details.set(True); app._toggle_details(); pump(root, 0.2)
app.copy_log(); check("Copy puts the log on the clipboard", "phone logged in" in root.clipboard_get())

app.core.last_heard -= 10; pump(root, 1.0)
check("silent phone -> Phone lost", app.status_var.get() == "Phone lost")

# error panel + retry
FakeServer.fail = RuntimeError("No Bluetooth adapter found")
app.retry(); pump(root, 0.5)
check("error panel shown", app.err_frame.winfo_ismapped() and "No Bluetooth adapter" in app.err_var.get())
check("status row hidden while error", not app.status_row.winfo_ismapped())
FakeServer.fail = None; app.retry(); pump(root, 0.6)
check("Retry works: error gone, status back", not app.err_frame.winfo_ismapped() and app.status_row.winfo_ismapped())

app.quit()
check("Quit closes the window", app.closing)
print("\nALL PASSED" if not fails else f"\nFAILED: {fails}"); sys.exit(1 if fails else 0)
