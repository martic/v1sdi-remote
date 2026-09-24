#!/usr/bin/env python3
"""v1sdi_midi.py — USB-only control daemon for the Roland V-1SDI (pbcc).

Replaces the RS-232 route: everything runs over the USB-B "MIDI" cable.
Channel messages (CC18 fader, bank/PC) plus Roland SysEx DT1 writes and RQ1
reads against the V-1SDI Parameter Address Map (V-1SDI Reference Manual
Ver.1.5). Requires the system program at Ver.1.5+.

Run: python3 v1sdi_midi.py [--port-name "V-1SDI"] [--listen 127.0.0.1:8789]
HTTP endpoints (GET for Companion) — see SPEC.md.
"""

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

try:
    import rtmidi  # pip install python-rtmidi
except ImportError:
    raise SystemExit("pip install python-rtmidi")

# SysEx framing: F0 41 (Roland) 10 (Device ID) 00 00 00 31 (V-1SDI model)
#   DT1 (write): 12 <addr> <data..> sum F7
#   RQ1 (read):  11 <addr> <size..> sum F7  -> reply DT1
MODEL_ID = [0x00, 0x00, 0x00, 0x31]
DEVICE_ID = 0x10

# Parameter Address Map (Reference Manual p.14+)
ADDR = {
    "video_sel_a":  (0x71, 0x03, 0x08),   # 00-03 Input 1-4 (PGM/bus A)
    "video_sel_b":  (0x71, 0x03, 0x09),   # 00-03 Input 1-4 (PST/bus B)
    "pinp_button":  (0x71, 0x03, 0x0A),   # 00/01
    "split_button": (0x71, 0x03, 0x0B),   # 00/01
    "trs_pattern":  (0x71, 0x03, 0x0C),   # 00 WIPE 01 MIX 02 CUT
    "dsk_button":   (0x71, 0x03, 0x0D),   # 00/01
    "memory_sel":   (0x73, 0x01, 0x00),   # 00-07 MEMORY 1-8
    "freeze_btn":   (0x73, 0x04, 0x00),   # 00 off 20 long-press 40 on
}
# Read-only status (LED state mirrors actual unit state)
STATUS_LED = {
    "pgm_led":    (0x73, 0x02, 0x11),     # VDOSEL1A
    "pst_led":    (0x73, 0x02, 0x15),     # VDOSEL1B
    "dsk_led":    (0x73, 0x02, 0x0F),
    "freeze_led": (0x73, 0x02, 0x02),
}
CC_FADER = 18           # A/B fader: 0=bus A end, 127=bus B end
BANK_PC = {             # alternative input-select path (channel messages)
    "a": (0x00, 0x00),
    "b": (0x01, 0x00),
    "mem": (0x50, 0x00),
}


def roland_checksum(data):
    s = 0
    for b in data:
        s = (s + b) & 0x7F
    return (128 - s) & 0x7F


def dt1(addr3, values):
    payload = list(addr3) + list(values)
    return [0xF0, 0x41, DEVICE_ID] + MODEL_ID + [0x12] + payload \
        + [roland_checksum(payload), 0xF7]


def rq1(addr3, size):
    payload = list(addr3) + list(size)
    return [0xF0, 0x41, DEVICE_ID] + MODEL_ID + [0x11] + payload \
        + [roland_checksum(payload), 0xF7]


class V1SDIMidi:
    def __init__(self, port_name=None, dry_run=False):
        self.dry_run = dry_run
        self.tx = None
        self.rxin = None
        self.last_response = None
        self.state = {"pgm": None, "pst": None, "dsk": None,
                      "freeze": None, "pinp": None, "split": None,
                      "trs": None}
        self._lock = threading.Lock()
        if not dry_run:
            self.tx = rtmidi.MidiOut()
            self.tx.open_virtual_port("v1sdi-out") if port_name is None \
                else self._open_named(self.tx, port_name)
            self.rxin = rtmidi.MidiIn()
            self.rxin.open_virtual_port("v1sdi-in") if port_name is None \
                else self._open_named_in(port_name)
            self.rxin.set_callback(self._on_msg)
            self.rxin.ignore_types(False, False, True)
            threading.Thread(target=self._poll_loop, daemon=True).start()
        else:
            class Fake:
                def send_message(self, m): self.last = m
            self.tx = Fake()

    def _open_named(self, out, name):
        for i, p in enumerate(out.get_ports()):
            if name.lower() in p.lower():
                out.open_port(i)
                return
        raise SystemExit(f"MIDI out port containing '{name}' not found: {out.get_ports()}")

    def _open_named_in(self, name):
        for i, p in enumerate(self.rxin.get_ports()):
            if name.lower() in p.lower():
                self.rxin.open_port(i)
                return
        raise SystemExit(f"MIDI in port containing '{name}' not found: {self.rxin.get_ports()}")

    # ---- low level -----------------------------------------------------------
    def _send_sysex(self, msg):
        self.tx.send_message(msg)

    def write_param(self, name, value):
        addr = ADDR[name]
        with self._lock:
            self._send_sysex(dt1(addr, [value]))
        self.state_update(name, value)
        self.last_response = "DT1 sent"
        return "DT1 sent"

    def read_status(self, name):
        addr = STATUS_LED[name]
        with self._lock:
            self._send_sysex(rq1(addr, [0x00, 0x00, 0x01]))
        return "RQ1 sent"

    def _on_msg(self, event, data=None):
        """Parse DT1 replies and periodic broadcasts from the switcher."""
        m = event[0]
        # F0 41 dev 00 00 00 31 12 addr3 data... sum F7
        # indices: 0=41@1, dev@2, model@3-6, cmd12@7, addr@8-10, data@11+
        if len(m) < 13 or m[0] != 0xF0 or m[1] != 0x41 or m[7] != 0x12:
            return
        addr = list(m[8:11])
        data = list(m[11:-2]) if len(m) > 13 else [m[11]]
        with self._lock:
            if addr == list(ADDR["video_sel_a"]):
                self.state["pgm"] = data[0] + 1
            elif addr == list(ADDR["video_sel_b"]):
                self.state["pst"] = data[0] + 1
            elif addr == list(ADDR["trs_pattern"]):
                self.state["trs"] = ["wipe", "mix", "cut"][data[0]]
            elif addr == list(ADDR["pinp_button"]):
                self.state["pinp"] = data[0]
            elif addr == list(ADDR["split_button"]):
                self.state["split"] = data[0]
            elif addr == list(ADDR["dsk_button"]):
                self.state["dsk"] = data[0]
            self.last_response = f"read {addr} -> {data}"

    def _poll_loop(self):
        """RQ1-poll the switcher so /status mirrors the unit, not our echo."""
        fast = [ADDR["video_sel_a"], ADDR["video_sel_b"]]
        slow = [a for k, a in ADDR.items()
                if k in ("pinp_button", "split_button", "dsk_button", "trs_pattern")]
        n = 0
        while True:
            for addr in fast:
                try:
                    self._send_sysex(rq1(addr, [0, 0, 1]))
                except Exception:
                    pass
                time.sleep(0.25)
            n += 1
            if n % 8 == 0:  # slow params every ~4s
                for addr in slow:
                    try:
                        self._send_sysex(rq1(addr, [0, 0, 1]))
                        time.sleep(0.05)
                    except Exception:
                        pass

    def fader(self, value):
        with self._lock:
            self.tx.send_message([0xB0, CC_FADER, max(0, min(127, value))])
        self.last_response = f"fader={value}"

    # ---- high level ----------------------------------------------------------
    def state_update(self, name, value):
        if name == "video_sel_a":
            self.state["pgm"] = value + 1
        elif name == "video_sel_b":
            self.state["pst"] = value + 1
        elif name in ("dsk_button", "freeze_btn", "pinp_button", "split_button"):
            self.state[name.split("_")[0]] = value
        elif name == "trs_pattern":
            self.state["trs"] = ["wipe", "mix", "cut"][value]

    def select(self, bus, ch):
        name = "video_sel_a" if bus == "a" else "video_sel_b"
        return self.write_param(name, ch - 1)

    def cut(self):
        """Instant cut: pattern=CUT then jump the fader to the other end."""
        cur = self.state.get("trs")
        self.write_param("trs_pattern", 2)
        self.fader(0 if (self.state.get("pgm") or 1) else 127)  # placeholder jump
        return "cut executed"

    def auto(self, steps=8, interval=0.05):
        """Timed transition: ramp the A/B fader (same as pressing AUTO)."""
        start, end = (0, 127)
        for i in range(1, steps + 1):
            self.fader(start + (end - start) * i // steps)
            time.sleep(interval)
        return "auto executed"

    def dispatch(self, path, q):
        p = path.strip("/")
        ch = q.get("ch")
        if p in ("pgm", "pst"):
            if not ch or not ch.isdigit() or not 1 <= int(ch) <= 4:
                return {"error": "ch must be 1..4"}
            return {"sent": self.select("a" if p == "pgm" else "b", int(ch))}
        if p == "cut":
            return {"sent": self.cut()}
        if p == "auto":
            return {"sent": self.auto()}
        if p == "trs":
            e = q.get("effect")
            if e not in ("0", "1", "2"):
                return {"error": "effect: 0=wipe 1=mix 2=cut"}
            return {"sent": self.write_param("trs_pattern", int(e))}
        if p in ("pip", "split", "dsk"):
            name = {"pip": "pinp_button", "split": "split_button",
                    "dsk": "dsk_button"}[p]
            cur = self.state.get(p if p != "pip" else "pinp")
            return {"sent": self.write_param(name, 0 if cur else 1)}
        if p == "freeze":
            return {"sent": self.write_param("freeze_btn", 0x40)}
        if p == "mem":
            m = q.get("m")
            if not m or not m.isdigit() or not 0 <= int(m) <= 7:
                return {"error": "m must be 0..7"}
            return {"sent": self.write_param("memory_sel", int(m))}
        if p == "status":
            return {"state": self.state, "last_response": self.last_response,
                    "dry_run": self.dry_run}
        return {"error": "unknown endpoint"}

    def close(self):
        if self.tx and not self.dry_run:
            self.tx.close_port()
        if self.rxin:
            self.rxin.close_port()


def build_server(v):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            q = {k: x[0] for k, x in parse_qs(u.query).items()}
            result = v.dispatch(u.path, q)
            self._json(result, 200 if "error" not in result else 400)

        def log_message(self, fmt=None, *args, **kwargs):
            pass

    return Handler


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port-name", default=None,
                    help='substring of the MIDI port name, e.g. "V-1SDI"')
    ap.add_argument("--listen", default="127.0.0.1:8789")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    v = V1SDIMidi(args.port_name, args.dry_run)
    host, port = args.listen.rsplit(":", 1)
    print(f"v1sdi-midi {'(dry-run)' if args.dry_run else ''}, HTTP {args.listen}")
    try:
        ThreadingHTTPServer((host, int(port)), build_server(v)).serve_forever()
    finally:
        v.close()
