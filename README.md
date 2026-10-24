# PC Bridge

Turn your Android phone into a wireless **keyboard, mouse and gamepad** for your PC over **Bluetooth Low Energy (BLE)**. No Bluetooth pairing, no USB receiver.

A Cordova phone app talks to a Python receiver on the PC. The receiver creates virtual input devices (via Linux `uinput`), so the desktop and games see a real keyboard, mouse and controller.

> **Status: experimental (v0.1.0).** The Linux receiver and the Android app work on the author's hardware. Windows support is planned but not written. **Read the [security warning](#security-warning) before using it.**

---

## Security warning

This project is an experiment and is **not safe for sensitive use yet**.

- **Messages are signed but not encrypted.** Someone with a Bluetooth sniffer within range could read what you type, including passwords.
- **The login is a 4-digit code.** It stops casual connections, but it is not strong protection against a determined attacker.
- Anyone in the `uinput` group on your PC can inject input, so only add accounts you trust.

Do not use PC Bridge to type passwords, banking details or other secrets. Stronger login and encryption are the top planned items (see [Roadmap](#roadmap)). Until then, use it only in places you trust.

---

## Features

- **Keyboard**: on-screen keyboard with Shift, Ctrl, Alt and Win modifiers, Caps, arrow keys, F-keys, and multi-touch (several keys held at once). Sends physical keys, so your PC's own layout decides the characters.
- **Mouse pad**: drag to move, tap to click, two-finger tap for right click, two-finger drag for scroll, double-tap-and-hold for drag lock, left/right/middle buttons.
- **Gamepad**: virtual controller with A/B/X/Y, bumpers, triggers (as buttons), D-pad, Start/Select/Home, thumbstick clicks and two analog sticks.
- **No pairing**: the phone finds the PC automatically and connects.
- **Session code login**: the PC shows a 4-digit code; type it on the phone. The code itself is never sent over the air.
- **Signed messages with replay protection**.
- **Safety first**: a heartbeat every second. If the PC hears nothing for 3 seconds, it releases every held key and button, so nothing gets stuck.
- **Emergency STOP** button on the PC.
- **Linux app**: a window with the code, status and log, packaged as a `.deb`.

## How it works

```
 Phone (Android, Cordova)                 PC (Linux, Python)
 ┌──────────────────────┐   BLE (GATT)   ┌──────────────────────────┐
 │ Keyboard / mouse /   │ ─────────────► │ core.py   login, checks, │
 │ gamepad screens      │                │           heartbeat      │
 │ app.js (BLE client)  │ ◄───────────── │ injector  uinput devices │
 └──────────────────────┘   replies      │ gui.py    window         │
                                         └──────────────────────────┘
```

- The **PC is the BLE peripheral**: it advertises as `PCBridge` with a private service.
- The **phone is the BLE central**: it scans, connects and logs in.
- Every message is a few bytes: `[type][sequence][payload][4-byte signature]`, within BLE's 20-byte write limit.
- Keys use standard USB HID usage codes; the PC maps them to Linux key codes.

## Tested on

| Part | Details |
|---|---|
| PC | Lenovo Yoga 260, Pop!_OS, BlueZ 5.72, Bluetooth 4.2 adapter |
| Phone | itel A70 (Android) |
| Python | 3.12 |

Other hardware and distributions are untested. The PC's Bluetooth adapter must support the **BLE peripheral role**.

## Requirements

**PC (Linux)**
- A Bluetooth adapter that supports BLE peripheral mode, and BlueZ
- Python 3.12 with `python3-tk` (only needed to run from source)
- Access to `/dev/uinput` (the installer sets this up)

**Phone**
- Android phone with Bluetooth Low Energy
- To build the app yourself: Node.js 20.5+, JDK 17, Android SDK (platform 34, build-tools 34), Cordova with `android@13`

## Install the PC app (Linux, `.deb`)

Build the installer from source (see [Build from source](#build-from-source)), then:

```
sudo apt install ./pcbridge_0.1.0_amd64.deb
```

The installer puts the app in `/opt/pcbridge/`, adds a **PC Bridge** entry to the app menu, sets up `/dev/uinput` access, and adds the installing user to the `uinput` group. If the app says it cannot open `/dev/uinput`, log out and back in once.

Remove it with:

```
sudo apt remove pcbridge
```

The bundle is built on the machine that will run it and depends on that system's C library, so it may not run on older distributions.

## Install the phone app

The app is currently a **debug build** installed over USB.

```
cd phone-app
npm install
cordova platform add android@13
cordova plugin add cordova-plugin-ble-central
adb devices
cordova run android
```

`adb devices` must list your phone as `device` (USB debugging on, USB mode "File transfer"). Or copy `platforms/android/app/build/outputs/apk/debug/app-debug.apk` to the phone and install it by hand.

## Use it

1. Open **PC Bridge** on the PC. The window shows a 4-digit code.
2. Open the phone app. Allow the Bluetooth ("Nearby devices") permission on first launch.
3. The phone finds `PCBridge` and connects. Type the code when asked.
4. Tap **Open keyboard & mouse** or **Open gamepad**.
5. **STOP** on the PC window ignores the phone and releases everything. **START** resumes.

Keep the phone app in the foreground while using it. Android limits background Bluetooth.

## Run from source

```
cd pc-app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python gui.py
```

Safe test that injects nothing (terminal version):

```
python ble_peripheral.py --print-only
```

Terminal commands while it runs: `/stop`, `/go`, `/status`.

## Build from source

Run inside `pc-app` with the environment active:

```
python tests/test_core.py                 # offline tests, no Bluetooth needed
bash packaging/build_bundle.sh            # PyInstaller bundle in dist/pcbridge/
bash packaging/build_deb.sh 0.1.0         # installer: pcbridge_0.1.0_amd64.deb
```

Raise the version number every time you install a new build. See `CHEATSHEET.md` for the full workflow.

## Project layout

```
pc-app/
  core.py             Bluetooth server, login, signatures, heartbeat, watchdog
  gui.py              tkinter window
  cli.py              terminal front end
  ble_peripheral.py   small wrapper for the terminal version
  protocol.py         message types, constants, signing helpers
  keymap_linux.py     USB HID code to Linux key code table
  injector_linux.py   uinput virtual keyboard, mouse and gamepad
  paths.py            settings, log and lock file locations
  packaging/          PyInstaller and .deb build scripts, menu entry, udev rule
  tests/              offline tests with fake Bluetooth and fake devices
phone-app/
  www/                the app: HTML, CSS and JavaScript
  config.xml          Cordova configuration
```

`core.py` and `protocol.py` are kept independent of the operating system so a Windows receiver can reuse them and only swap the injector.

## Files the app creates

| Item | Location |
|---|---|
| Settings | `~/.config/pcbridge/settings.json` |
| Log | `~/.local/state/pcbridge/pcbridge.log` |
| Lock file (one instance only) | `~/.cache/pcbridge/lock` |

Typed keys and the session code are never written to the log. "Show each key" in the window is off by default.

## Known limitations

- Closing the window quits the app (no tray icon yet).
- A second launch says the app is already open, but does not bring the first window forward.
- One phone at a time.
- The phone app is a debug build and must stay in the foreground.
- L2/R2 on the gamepad are on/off buttons, not analog triggers. A virtual gamepad may not be recognised by every game.
- The Caps Lock light on the phone is only a guess of the PC's state and can drift.
- Pointer speed is applied on both the phone and the PC.
- The phone keyboard is a US layout.
- No emergency stop key on the PC's own keyboard yet (use the STOP button).

## Roadmap

1. **Security**: encryption of all traffic and a stronger login than a 4-digit code.
2. **Windows receiver**: same protocol and phone app, with a Windows injector (`SendInput`, and a virtual gamepad driver). The first step is a small test of whether BLE advertising works on Windows.
3. Release (signed) APK for the phone.
4. Tray icon, start at login, bring the first window to the front on a second launch.
5. Report the PC's real Caps Lock state to the phone; Home/End/PageUp/PageDown keys; analog triggers.

## Contributing

Issues and ideas are welcome. If you want to work on the security design or the Windows port, open an issue first so we can agree on the approach. Please run `python tests/test_core.py` before sending changes.

## License

No license has been chosen yet. Until one is added, all rights are reserved by the author.

## Acknowledgements

Built with [bless](https://github.com/kevincar/bless) (BLE server), [python-evdev](https://github.com/gvalkov/python-evdev) (virtual input devices), [PyInstaller](https://pyinstaller.org/) (packaging), and [Apache Cordova](https://cordova.apache.org/) with `cordova-plugin-ble-central` (Android app).
