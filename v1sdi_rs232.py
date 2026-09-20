#!/usr/bin/env python3
"""v1sdi_rs232.py — Control the Roland V-1SDI switcher over RS-232 from a
Raspberry Pi (Companion-friendly HTTP endpoints).

Run on the Pi:   python3 v1sdi_rs232.py --device /dev/ttyUSB0 --baud 38400
Dry-run (no serial): python3 v1sdi_rs232.py --dry-run
"""

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class V1SDI:
    """RS-232 command transport for the Roland V-1SDI.

    Commands are ASCII `stx<NAME>[:param];` terminated by ';'; the unit
    replies `ack` or `err`. Roland Pro-AV default serial: 38400 8N1.
    """

    def __init__(self, device=None, baud=38400, dry_run=False):
        self.dry_run = dry_run
        self.ser = None
        self.last_response = None
        self.pgm = None
        self.pst = None
        self._lock = threading.Lock()
        if not dry_run:
            import serial  # pip install pyserial
            self.ser = serial.Serial(device, baud, timeout=0.5,
                                     bytesize=8, parity="N", stopbits=1)
        self._drain()

    def _drain(self):
        if self.ser:
            self.ser.reset_input_buffer()

    def send(self, command: str) -> str:
        """Send 'PGM:1' style command; wraps in stx...; and reads ack/err."""
        wire = f"stx{command};"
        with self._lock:
            if self.dry_run:
                self.last_response = "ack(dry-run)"
            else:
                self.ser.reset_input_buffer()
                self.ser.write(wire.encode())
                time.sleep(0.05)
                resp = self.ser.read(64).decode(errors="replace").strip()
                self.last_response = resp or "(no reply)"
        self._track(command)
        return self.last_response

    def _track(self, command: str):
        name = command.split(":")[0].upper()
        try:
            arg = int(command.split(":")[1])
        except (IndexError, ValueError):
            arg = None
        if name == "PGM" and arg is not None:
            self.pgm = arg + 1
        elif name == "PST" and arg is not None:
            self.pst = arg + 1

    def dispatch(self, path: str, q: dict):
        p = path.strip("/")
        if p == "pgm":
            ch = q.get("ch")
            if not ch or not ch.isdigit() or not 1 <= int(ch) <= 4:
                return {"error": "ch must be 1..4"}
            return {"sent": self.send(f"PGM:{int(ch) - 1}"), "pgm": int(ch)}
        if p == "pst":
            ch = q.get("ch")
            if not ch or not ch.isdigit() or not 1 <= int(ch) <= 4:
                return {"error": "ch must be 1..4"}
            return {"sent": self.send(f"PST:{int(ch) - 1}"), "pst": int(ch)}
        if p == "auto":
            return {"sent": self.send("ATO")}
        if p == "cut":
            return {"sent": self.send("CUT")}
        if p == "trs":
            effect = q.get("effect")
            if effect not in ("0", "1"):
                return {"error": "effect must be 0 (wipe) or 1 (mix)"}
            return {"sent": self.send(f"TRS:{effect}")}
        if p == "freeze":
            on = q.get("state") == "on"
            return {"sent": self.send(f"FRZ:{1 if on else 0}")}
        if p == "dsk":
            on = q.get("state") == "on"
            return {"sent": self.send(f"DSK:{1 if on else 0}")}
        if p == "ips":
            sel = q.get("src")
            if sel not in ("0", "1", "2"):
                return {"error": "src must be 0 (auto), 1 (sdi3), 2 (hdmi3)"}
            return {"sent": self.send(f"IPS:{sel}")}
        if p == "status":
            return {"pgm": self.pgm, "pst": self.pst,
                    "last_response": self.last_response,
                    "dry_run": self.dry_run}
        return {"error": "unknown endpoint"}

    def close(self):
        if self.ser:
            self.ser.close()


def build_server(v: V1SDI):
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
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            result = v.dispatch(u.path, q)
            self._json(result, 200 if "error" not in result else 400)

        def log_message(self, fmt=None, *args, **kwargs):
            pass

    return Handler


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=38400)
    ap.add_argument("--listen", default="127.0.0.1:8788")
    ap.add_argument("--dry-run", action="store_true",
                    help="log commands instead of opening a serial port")
    args = ap.parse_args()

    v = V1SDI(None if args.dry_run else args.device, args.baud, args.dry_run)
    host, port = args.listen.rsplit(":", 1)
    print(f"v1sdi-rs232 {'(dry-run)' if args.dry_run else args.device}@{args.baud}, HTTP {args.listen}")
    try:
        ThreadingHTTPServer((host, int(port)), build_server(v)).serve_forever()
    finally:
        v.close()
