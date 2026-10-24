// PC Bridge - connection + status interface (BLE central)
// Scans continuously, lists nearby devices, auto-connects to the laptop,
// shows live status (signal, uptime, ping time, counters), and reconnects on drop.

const SERVICE = 'b5a3f001-7c1e-4b8a-9d2f-3e6a1c4d8f10';
const RX = 'b5a3f002-7c1e-4b8a-9d2f-3e6a1c4d8f10'; // phone -> PC (write)
const TX = 'b5a3f003-7c1e-4b8a-9d2f-3e6a1c4d8f10'; // PC -> phone (notify)
const DEVICE_NAME = 'PCBridge';
const SCAN_SECONDS = 8;
const STALE_MS = 20000;   // forget devices not seen for this long
const MAX_LOG_LINES = 300;

// Message format (both directions): [type:1][seq:1][payload: up to 18 bytes]
const T_HEARTBEAT = 0x01, T_PING = 0x02, T_TEXT = 0x10; // phone -> PC (TEXT works both ways)
const T_ACK = 0x81, T_PONG = 0x82;                      // PC -> phone, echo the request's seq
// Login handshake (see ble_peripheral.py for the full description)
const T_HELLO = 0x04, T_AUTH = 0x05;                    // phone -> PC, unsigned
const T_CHALLENGE = 0x83, T_AUTH_OK = 0x84, T_AUTH_FAIL = 0x85; // PC -> phone, unsigned
const NONCE_LEN = 8, PROOF_LEN = 8, TAG_LEN = 4;        // every other message ends with a 4-byte signature
const HB_INTERVAL_MS = 1000;  // heartbeat rate
const HB_STALE_MS = 3000;     // no reply for this long: show "No reply"
const HB_DEAD_MS = 8000;      // no reply for this long: drop the link and reconnect

let deviceId = null;
let state = 'idle'; // idle | scanning | connecting | connected
let scanTimer = null;
let rescanTimer = null;
let rssiTimer = null;
let uptimeTimer = null;

let devices = {}; // id -> { name, rssi, seen, isBridge }
let connectedAt = 0;
let sent = 0;
let received = 0;
let renderQueued = false;
let txSeq = 0;        // sequence number for messages we send
let pending = {};     // seq -> time sent, to measure round-trip
let lastAckAt = 0;
let hbTimer = null;
let authed = false;       // true once the code handshake has succeeded
let sessionKey = null;
let myNonce = null, pcNonce = null;
let typedCode = null;     // code just typed by the user
let codeInUse = null;     // last accepted code, kept in memory so reconnects need no retyping
let attemptCode = null;
let helloTimer = null, helloTries = 0;
let lastReady = false;
const readyListeners = [];

const $ = (id) => document.getElementById(id);

// ---------- crypto: SHA-256 + HMAC in plain JS (works in every WebView) ----------

const SHA_K = (function () {
  const k = [];
  for (let n = 2; k.length < 64; n++) {
    let prime = true;
    for (let d = 2; d * d <= n; d++) if (n % d === 0) { prime = false; break; }
    if (prime) k.push(Math.floor((Math.cbrt(n) % 1) * 4294967296) >>> 0);
  }
  return k;
})();
const SHA_H0 = [2, 3, 5, 7, 11, 13, 17, 19].map((n) => Math.floor((Math.sqrt(n) % 1) * 4294967296) >>> 0);

function rotr(x, n) { return (x >>> n) | (x << (32 - n)); }

function sha256(msg) {
  const l = msg.length;
  const buf = new Uint8Array(((l + 9 + 63) >> 6) << 6);
  buf.set(msg);
  buf[l] = 0x80;
  const dv = new DataView(buf.buffer);
  dv.setUint32(buf.length - 4, (l * 8) >>> 0);
  dv.setUint32(buf.length - 8, Math.floor(l / 0x20000000));
  const H = SHA_H0.slice();
  const w = new Uint32Array(64);
  for (let off = 0; off < buf.length; off += 64) {
    for (let i = 0; i < 16; i++) w[i] = dv.getUint32(off + i * 4);
    for (let i = 16; i < 64; i++) {
      const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
      const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
    }
    let a = H[0], b = H[1], c = H[2], d = H[3], e = H[4], f = H[5], g = H[6], h = H[7];
    for (let i = 0; i < 64; i++) {
      const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const ch = (e & f) ^ (~e & g);
      const t1 = (h + S1 + ch + SHA_K[i] + w[i]) | 0;
      const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const t2 = (S0 + maj) | 0;
      h = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
    }
    H[0] = (H[0] + a) | 0; H[1] = (H[1] + b) | 0; H[2] = (H[2] + c) | 0; H[3] = (H[3] + d) | 0;
    H[4] = (H[4] + e) | 0; H[5] = (H[5] + f) | 0; H[6] = (H[6] + g) | 0; H[7] = (H[7] + h) | 0;
  }
  const out = new Uint8Array(32);
  const odv = new DataView(out.buffer);
  for (let i = 0; i < 8; i++) odv.setUint32(i * 4, H[i] >>> 0);
  return out;
}

function concat() {
  let n = 0;
  for (let i = 0; i < arguments.length; i++) n += arguments[i].length;
  const out = new Uint8Array(n);
  let o = 0;
  for (let i = 0; i < arguments.length; i++) { out.set(arguments[i], o); o += arguments[i].length; }
  return out;
}

function strBytes(s) {
  const o = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) o[i] = s.charCodeAt(i) & 255;
  return o;
}

function hmac(key, msg) {
  if (key.length > 64) key = sha256(key);
  const ipad = new Uint8Array(64), opad = new Uint8Array(64);
  for (let i = 0; i < 64; i++) {
    const k = i < key.length ? key[i] : 0;
    ipad[i] = k ^ 0x36;
    opad[i] = k ^ 0x5c;
  }
  return sha256(concat(opad, sha256(concat(ipad, msg))));
}

function randomBytes(n) {
  const o = new Uint8Array(n);
  crypto.getRandomValues(o);
  return o;
}

function bytesEqual(a, b) {
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a[i] ^ b[i];
  return d === 0;
}

// Signature on every message after login. dir: 'P' = phone to PC, 'L' = laptop to phone.
function makeTag(key, dir, type, seq, payload) {
  return hmac(key, concat(strBytes(dir), Uint8Array.of(type, seq), payload)).subarray(0, TAG_LEN);
}

// ---------- end crypto ----------

// ---------- log ----------

function log(msg, cls) {
  const t = new Date().toLocaleTimeString();
  const line = document.createElement('div');
  line.className = cls || 'sys';
  line.textContent = '[' + t + '] ' + msg;
  const box = $('log');
  box.appendChild(line);
  while (box.childNodes.length > MAX_LOG_LINES) box.removeChild(box.firstChild);
  box.scrollTop = box.scrollHeight;
}

function copyLog() {
  const text = $('log').innerText;
  const done = () => log('Log copied.');
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done, fallbackCopy);
  } else {
    fallbackCopy();
  }
  function fallbackCopy() {
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); done(); } catch (e) { log('Copy failed.', 'err'); }
    document.body.removeChild(ta);
  }
}

// ---------- status ----------

function setStatus(text, cls) {
  $('status').textContent = text;
  $('status').className = cls || '';
  $('pingBtn').disabled = (cls !== 'connected');
  $('kbBtn').disabled = (cls !== 'connected');
  $('gpBtn').disabled = (cls !== 'connected');
  emitReady();
}

function setTarget(text) {
  $('target').textContent = text || '';
}

function fmtUptime(ms) {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  const pad = (n) => (n < 10 ? '0' : '') + n;
  return h > 0 ? h + ':' + pad(m % 60) + ':' + pad(s % 60) : pad(m) + ':' + pad(s % 60);
}

let countsQueued = false;
function updateCounts() {   // throttled: mouse moves send ~40 messages/s
  if (countsQueued) return;
  countsQueued = true;
  requestAnimationFrame(() => { countsQueued = false; $('countStat').textContent = sent + ' / ' + received; });
}

function resetLiveStats() {
  $('sigStat').textContent = '-';
  $('uptimeStat').textContent = '-';
  $('linkStat').textContent = '-';
}

function startLiveStats() {
  connectedAt = Date.now();
  lastAckAt = Date.now();
  $('linkStat').textContent = 'Logging in';
  uptimeTimer = setInterval(() => {
    $('uptimeStat').textContent = fmtUptime(Date.now() - connectedAt);
    const pn = performance.now();
    Object.keys(pending).forEach((k) => { if (pn - pending[k] > 10000) delete pending[k]; });
    if (state !== 'connected' || !authed) return;
    const silent = Date.now() - lastAckAt;
    if (silent > HB_DEAD_MS) {
      log('No reply from PC for ' + Math.round(silent / 1000) + 's. Reconnecting.', 'err');
      lastAckAt = Date.now();
      ble.disconnect(deviceId, () => {}, () => {});
    } else if (silent > HB_STALE_MS) {
      $('linkStat').textContent = 'No reply';
    }
  }, 1000);
  const readRssi = () => {
    if (state !== 'connected') return;
    ble.readRSSI(deviceId,
      (rssi) => {
        $('sigStat').textContent = rssi + ' dBm';
        if (devices[deviceId]) devices[deviceId].rssi = rssi;
        queueRender();
      },
      () => {});
  };
  readRssi();
  rssiTimer = setInterval(readRssi, 3000);
}

function startHeartbeat() {
  clearInterval(hbTimer);
  lastAckAt = Date.now();
  $('linkStat').textContent = 'Starting';
  hbTimer = setInterval(() => sendMsg(T_HEARTBEAT, null, null), HB_INTERVAL_MS);
  sendMsg(T_HEARTBEAT, null, null);
}

function stopLiveStats() {
  clearInterval(uptimeTimer);
  clearInterval(rssiTimer);
  clearInterval(hbTimer);
  uptimeTimer = rssiTimer = hbTimer = null;
  pending = {};
  resetLiveStats();
}

// ---------- devices panel ----------

function queueRender() {
  if (renderQueued) return;
  renderQueued = true;
  requestAnimationFrame(() => { renderQueued = false; renderDevices(); });
}

function renderDevices() {
  const now = Date.now();
  Object.keys(devices).forEach((id) => {
    if (id !== deviceId && now - devices[id].seen > STALE_MS) delete devices[id];
  });

  const list = Object.keys(devices).map((id) => Object.assign({ id: id }, devices[id]));
  list.sort((a, b) => {
    if ((b.id === deviceId) !== (a.id === deviceId)) return a.id === deviceId ? -1 : 1;
    if (a.isBridge !== b.isBridge) return a.isBridge ? -1 : 1;
    return b.rssi - a.rssi;
  });

  const box = $('devices');
  box.textContent = '';

  if (list.length === 0) {
    const e = document.createElement('div');
    e.className = 'empty';
    e.textContent = state === 'scanning' ? 'Searching for devices...' : 'No devices in range. Tap Rescan.';
    box.appendChild(e);
    return;
  }

  list.forEach((d) => {
    const row = document.createElement('div');
    row.className = 'dev' + (d.isBridge ? ' bridge' : '') + (d.id === deviceId && state === 'connected' ? ' connected' : '');

    const left = document.createElement('div');
    const name = document.createElement('div');
    name.className = 'name';
    name.textContent = (d.name || '(no name)') + (d.id === deviceId && state === 'connected' ? '  - connected' : '');
    const id = document.createElement('div');
    id.className = 'id';
    id.textContent = d.id;
    left.appendChild(name);
    left.appendChild(id);

    const rssi = document.createElement('div');
    rssi.className = 'rssi';
    rssi.textContent = d.rssi + ' dBm';

    row.appendChild(left);
    row.appendChild(rssi);
    row.addEventListener('click', () => onDeviceTapped(d.id));
    box.appendChild(row);
  });
}

function onDeviceTapped(id) {
  if (state === 'connected' || state === 'connecting') {
    log('Already ' + state + '. Tap Rescan to disconnect first.');
    return;
  }
  log('Manual connect to ' + id);
  connectTo(id);
}

// ---------- bytes ----------

function toText(buf) {
  return String.fromCharCode.apply(null, new Uint8Array(buf));
}

function encode(type, seq, payload) {
  const p = payload || new Uint8Array(0);
  const out = new Uint8Array(2 + p.length);
  out[0] = type;
  out[1] = seq;
  out.set(p, 2);
  return out.buffer;
}

function decode(buf) {
  const a = new Uint8Array(buf);
  if (a.length < 2) return null;
  return { type: a[0], seq: a[1], payload: a.subarray(2) };
}

// Write one frame. sign=true appends the 4-byte signature (everything except the handshake).
// opts: { noResponse: true } for fire-and-forget writes (mouse moves); { onDone(ok) } called when the write finishes.
let lastFailLog = 0;
function writeFrame(type, payload, label, sign, opts) {
  opts = opts || {};
  const seq = txSeq;
  txSeq = (txSeq + 1) & 0xFF;
  const body = payload || new Uint8Array(0);
  const full = sign ? concat(body, makeTag(sessionKey, 'P', type, seq, body)) : body;
  if (sign && (type === T_HEARTBEAT || type === T_PING)) pending[seq] = performance.now();  // only these get replies
  const ok = () => { sent++; updateCounts(); if (label) log('SENT: ' + label + ' (seq ' + seq + ')', 'tx'); if (opts.onDone) opts.onDone(true); };
  const fail = (err) => {
    delete pending[seq];
    if (Date.now() - lastFailLog > 1000) { lastFailLog = Date.now(); log('Write failed: ' + JSON.stringify(err), 'err'); }
    if (opts.onDone) opts.onDone(false);
  };
  const data = encode(type, seq, full);
  if (opts.noResponse) ble.writeWithoutResponse(deviceId, SERVICE, RX, data, ok, fail);
  else ble.write(deviceId, SERVICE, RX, data, ok, fail);
}

// Send a signed message. Only works after login. label = text to log on success (null = quiet).
function sendMsg(type, payload, label) {
  if (state !== 'connected' || !authed || !sessionKey) return;
  writeFrame(type, payload, label, true);
}

// Round-trip time for the reply to message `seq`, or null if unknown.
function takeRtt(seq) {
  if (pending[seq] === undefined) return null;
  const rtt = Math.round(performance.now() - pending[seq]);
  delete pending[seq];
  return rtt;
}

function onNotify(buf) {
  const m = decode(buf);
  if (!m) { log('RECEIVED: malformed message', 'err'); return; }

  if (m.type === T_CHALLENGE) return onChallenge(m);
  if (m.type === T_AUTH_OK) return onAuthOk(m);
  if (m.type === T_AUTH_FAIL) return onAuthFail(m);

  if (!authed || !sessionKey) { log('Ignored message before login (type 0x' + m.type.toString(16) + ')', 'err'); return; }
  if (m.payload.length < TAG_LEN) { log('RECEIVED: too short', 'err'); return; }
  const body = m.payload.subarray(0, m.payload.length - TAG_LEN);
  const tag = m.payload.subarray(m.payload.length - TAG_LEN);
  if (!bytesEqual(tag, makeTag(sessionKey, 'L', m.type, m.seq, body))) {
    log('RECEIVED: bad signature, dropped', 'err');
    return;
  }
  m.payload = body;
  received++;
  updateCounts();

  if (m.type === T_ACK) {
    lastAckAt = Date.now();
    const rtt = takeRtt(m.seq);
    $('linkStat').textContent = 'OK' + (rtt !== null ? ', ' + rtt + ' ms' : '');
  } else if (m.type === T_PONG) {
    lastAckAt = Date.now();
    const rtt = takeRtt(m.seq);
    if (rtt !== null) $('rttStat').textContent = rtt + ' ms';
    log('RECEIVED: PONG' + (rtt !== null ? ' (' + rtt + ' ms)' : ''), 'rx');
  } else if (m.type === T_TEXT) {
    log('RECEIVED: ' + toText(m.payload), 'rx');
  } else {
    log('RECEIVED: unknown type 0x' + m.type.toString(16) + ' seq ' + m.seq, 'err');
  }
}

// ---------- login (4-digit session code) ----------

function showAuth(msg) {
  closeKeyboard();   // the code box lives on the status screen
  closeGamepad();
  $('authMsg').textContent = msg;
  $('auth').classList.add('show');
  $('codeInput').focus();
}

function hideAuth() {
  $('auth').classList.remove('show');
  $('codeInput').value = '';
}

function resetAuth() {
  authed = false;
  sessionKey = null;
  myNonce = pcNonce = null;
  typedCode = null;
  clearTimeout(helloTimer);
  helloTries = 0;
  hideAuth();
  emitReady();
}

function sendHello() {
  myNonce = randomBytes(NONCE_LEN);
  pcNonce = null;
  writeFrame(T_HELLO, myNonce, null, false);
  clearTimeout(helloTimer);
  helloTimer = setTimeout(() => {
    if (state === 'connected' && !authed && !pcNonce && helloTries++ < 3) sendHello();
  }, 3000);
}

function authKeyMaterial(code) {
  return { key: strBytes(code), nonces: concat(myNonce, pcNonce) };
}

function onChallenge(m) {
  clearTimeout(helloTimer);
  if (m.payload.length !== NONCE_LEN || !myNonce) return;
  pcNonce = new Uint8Array(m.payload);
  const code = typedCode || codeInUse;
  if (!code) { showAuth('Enter the code shown on the laptop'); setStatus('Enter code', 'connecting'); return; }
  sendAuth(code);
}

function sendAuth(code) {
  attemptCode = code;
  typedCode = null;
  const k = authKeyMaterial(code);
  const proof = hmac(k.key, concat(strBytes('P'), k.nonces)).subarray(0, PROOF_LEN);
  setStatus('Logging in', 'connecting');
  writeFrame(T_AUTH, proof, null, false);
}

function onAuthOk(m) {
  if (!attemptCode || !pcNonce) return;
  const k = authKeyMaterial(attemptCode);
  const expected = hmac(k.key, concat(strBytes('L'), k.nonces)).subarray(0, PROOF_LEN);
  if (!bytesEqual(expected, m.payload)) {
    log('The laptop did not prove it knows the code. Disconnecting.', 'err');
    ble.disconnect(deviceId, () => {}, () => {});
    return;
  }
  sessionKey = hmac(k.key, concat(strBytes('K'), k.nonces));
  codeInUse = attemptCode;
  attemptCode = null;
  authed = true;
  hideAuth();
  setStatus('Connected', 'connected');
  log('Logged in.');
  startHeartbeat();
  queueRender();
}

function onAuthFail(m) {
  clearTimeout(helloTimer);
  const reason = m.payload[0], val = m.payload[1];
  codeInUse = null;
  attemptCode = null;
  typedCode = null;
  pcNonce = null;
  authed = false;
  sessionKey = null;
  setStatus('Enter code', 'connecting');
  if (reason === 1) {
    log('Locked out for ' + val + 's. The laptop made a new code.', 'err');
    showAuth('Too many wrong codes. Locked for ' + val + 's. Then enter the NEW code from the laptop.');
  } else {
    log('Wrong code (' + val + ' tries left).', 'err');
    showAuth('Wrong code. ' + val + ' tries left.');
  }
}

function submitCode() {
  const v = $('codeInput').value.trim();
  if (!/^\d{4}$/.test(v)) { $('authMsg').textContent = 'The code is 4 digits.'; return; }
  $('codeInput').value = '';
  if (state !== 'connected') return;
  typedCode = v;
  setStatus('Logging in', 'connecting');
  if (pcNonce) sendAuth(v); else sendHello();
}

// Our service UUID as it appears in raw advertising data (little-endian).
const SERVICE_BYTES = SERVICE.replace(/-/g, '').match(/../g).map((h) => parseInt(h, 16)).reverse();

function advHasService(adv) {
  if (!(adv instanceof ArrayBuffer)) return false;
  const a = new Uint8Array(adv);
  for (let i = 0; i + 16 <= a.length; i++) {
    let ok = true;
    for (let j = 0; j < 16; j++) {
      if (a[i + j] !== SERVICE_BYTES[j]) { ok = false; break; }
    }
    if (ok) return true;
  }
  return false;
}

// ---------- scanning + connecting ----------

function startScanning() {
  if (state === 'scanning' || state === 'connecting' || state === 'connected') return;
  state = 'scanning';
  setStatus('Searching', 'searching');
  setTarget('');
  log('Scanning...');
  queueRender();

  // Unfiltered scan so the devices panel shows everything nearby.
  // The laptop is recognised by name or by our service UUID in its advertisement.
  ble.startScan([], onDeviceFound, (err) => {
    log('Scan error: ' + JSON.stringify(err), 'err');
    state = 'idle';
    scheduleRescan(3000);
  });

  scanTimer = setTimeout(() => {
    if (state !== 'scanning') return;
    ble.stopScan(() => {}, () => {});
    state = 'idle';
    startScanning();
  }, SCAN_SECONDS * 1000);
}

function scheduleRescan(ms) {
  clearTimeout(rescanTimer);
  rescanTimer = setTimeout(startScanning, ms);
}

function onDeviceFound(device) {
  const isBridge = device.name === DEVICE_NAME || advHasService(device.advertising);
  const known = devices[device.id];
  devices[device.id] = {
    name: device.name || (known && known.name) || '',
    rssi: device.rssi,
    seen: Date.now(),
    isBridge: isBridge || (known && known.isBridge) || false
  };
  if (!known) log('Found: ' + (device.name || '(no name)') + ' ' + device.id + (isBridge ? '  <- PC Bridge' : ''));
  queueRender();

  if (state === 'scanning' && devices[device.id].isBridge) connectTo(device.id);
}

function connectTo(id) {
  clearTimeout(scanTimer);
  clearTimeout(rescanTimer);
  ble.stopScan(() => {}, () => {});
  state = 'connecting';
  deviceId = id;
  const d = devices[id];
  setStatus('Connecting', 'connecting');
  setTarget(d && d.name ? d.name : id);
  log('Connecting to ' + id + '...');
  queueRender();

  // The failure callback also fires later if the link drops.
  ble.connect(id, onConnected, onDisconnected);
}

function onConnected(peripheral) {
  const services = (peripheral.services || []).map((s) => s.toLowerCase());
  if (services.indexOf(SERVICE) === -1) {
    log('That device does not offer the PC Bridge service. Disconnecting.', 'err');
    ble.disconnect(deviceId, () => {}, () => {});
    return;
  }

  state = 'connected';
  setStatus('Logging in', 'connecting');
  log('Link up. Logging in...');
  startLiveStats();
  queueRender();

  log('Services: ' + services.join(', '));
  (peripheral.characteristics || []).forEach((c) =>
    log('  char ' + c.characteristic + ' in ' + c.service + ' [' + (c.properties || []).join(',') + ']'));

  ble.startNotification(deviceId, SERVICE, TX,
    onNotify,
    (err) => log('Notify subscribe failed: ' + JSON.stringify(err), 'err'));
  log('Subscribed to notifications.');
  resetAuth();
  helloTries = 0;
  setTimeout(() => { if (state === 'connected') sendHello(); }, 300);
}

function onDisconnected(info) {
  resetAuth();
  log('Disconnected: ' + (typeof info === 'string' ? info : JSON.stringify(info)), 'err');
  stopLiveStats();
  clearTimeout(scanTimer);
  state = 'idle';
  deviceId = null;
  setStatus('Disconnected', 'disconnected');
  setTarget('');
  queueRender();
  scheduleRescan(1000);
}

function sendPing() {
  sendMsg(T_PING, null, 'PING');
}

function manualRescan() {
  clearTimeout(scanTimer);
  clearTimeout(rescanTimer);
  devices = {};
  queueRender();
  const go = () => { state = 'idle'; startScanning(); };
  if (deviceId) {
    ble.disconnect(deviceId, go, go);
  } else {
    ble.stopScan(go, go);
  }
}

// ---------- input bridge (used by keyboard.js) ----------

function isReady() { return state === 'connected' && authed && !!sessionKey; }

function emitReady() {
  const r = isReady();
  if (r === lastReady) return;
  lastReady = r;
  readyListeners.forEach((fn) => { try { fn(r); } catch (e) { log('Listener error: ' + e, 'err'); } });
}

function openKeyboard() {
  if (!isReady()) { log('Log in first.', 'err'); return; }
  closeGamepad();
  $('kbView').classList.add('show');
  document.body.classList.add('kbmode');
}

function closeKeyboard() {
  if (!$('kbView').classList.contains('show')) return;
  if (window.Keyboard) window.Keyboard.onHide();   // releases every key and button
  $('kbView').classList.remove('show');
  document.body.classList.remove('kbmode');
}

function openGamepad() {
  if (!isReady()) { log('Log in first.', 'err'); return; }
  closeKeyboard();
  $('gpView').classList.add('show');
  document.body.classList.add('kbmode');
}

function closeGamepad() {
  if (!$('gpView').classList.contains('show')) return;
  if (window.PadUI) window.PadUI.onHide();   // releases every button, centres the sticks
  $('gpView').classList.remove('show');
  document.body.classList.remove('kbmode');
}

window.Bridge = {
  ready: isReady,
  // type: message type byte, payload: array/Uint8Array, opts: see writeFrame. Returns false if not logged in.
  send(type, payload, opts) {
    if (!isReady()) return false;
    writeFrame(type, payload ? Uint8Array.from(payload) : null, null, true, opts);
    return true;
  },
  onReady(fn) { readyListeners.push(fn); fn(isReady()); },
  openKeyboard: openKeyboard,
  closeKeyboard: closeKeyboard,
  openGamepad: openGamepad,
  closeGamepad: closeGamepad
};

document.addEventListener('deviceready', () => {
  log('App ready.');
  // Keep the screen on while the app is open (needs the insomnia plugin; skipped if absent).
  if (window.plugins && window.plugins.insomnia) window.plugins.insomnia.keepAwake();
  $('pingBtn').addEventListener('click', sendPing);
  $('kbBtn').addEventListener('click', openKeyboard);
  $('gpBtn').addEventListener('click', openGamepad);
  // Android back button: leave the keyboard first, then exit.
  document.addEventListener('backbutton', () => {
    if ($('kbView').classList.contains('show')) closeKeyboard();
    else if ($('gpView').classList.contains('show')) closeGamepad();
    else if (navigator.app && navigator.app.exitApp) navigator.app.exitApp();
  }, false);
  // App sent to background: never leave a key held down on the PC.
  document.addEventListener('pause', () => { if (window.Keyboard) window.Keyboard.releaseAll(true); if (window.PadUI) window.PadUI.releaseAll(true); }, false);
  $('rescanBtn').addEventListener('click', manualRescan);
  $('codeBtn').addEventListener('click', submitCode);
  $('codeInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') submitCode(); });
  $('clearBtn').addEventListener('click', () => { $('log').textContent = ''; });
  $('copyBtn').addEventListener('click', copyLog);
  $('toggleLogBtn').addEventListener('click', () => {
    const hidden = $('log').classList.toggle('hidden');
    $('toggleLogBtn').textContent = hidden ? 'Show' : 'Hide';
  });
  renderDevices();

  ble.isEnabled(
    () => startScanning(),
    () => {
      log('Bluetooth is off. Asking to enable...');
      ble.enable(() => startScanning(), () => {
        setStatus('Bluetooth off', 'disconnected');
        log('Please turn Bluetooth on, then tap Rescan.', 'err');
      });
    });
}, false);
