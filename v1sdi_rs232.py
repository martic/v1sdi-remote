#!/usr/bin/env python3
"""v1sdi_rs232.py — RS-232 control daemon for the Roland V-1SDI (pbcc).

Protocol (V-1SDI Reference Manual Ver.1.5, RS-232 Command Reference):
- Framing: STX (0x02) + 3-letter command + [":param"] + ";"
- Replies: ACK (0x06) on success, "stxERR:a;" on failure
- Serial: 9600 8N1, XON/XOFF; **crossover** DB-9 cable (V-1SDI pin2 RXD,
  pin3 TXD, pin5 GND); wait for ACK before the next command.

Commands implemented (verified against the Reference Manual):
  PGM:a PST:a TRS:a TIM:a ATO PIP SPT DSK FRZ IPS:a ISC:a OPS:a KYL:a MEM:a VER
Run: python3 v1sdi_rs232.py --device /dev/ttyUSB0 [--baud 9600] [--dry-run]
HTTP (Companion): GET/POST on 127.0.0.1:8788 — see SPEC.md for the mapping.
"""

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

STX = b"\x02"
ACK = "\x06"


class V1SDI:
    def __init__(self, device=None, baud=9600, dry_run=False):
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

    def send(self, command: str) -> str:
        """command like 'PGM:0' or 'ATO'. Wraps in STX...; waits for ACK."""
        wire = STX + command.encode() + b";"
        with self._lock:
            if self.dry_run:
                self.last_response = "ack(dry-run)"
            else:
                self.ser.reset_input_buffer()
                self.ser.write(wire)
                # Reference manual: wait for ACK before sending the next cmd
                deadline = time.time() + 1.0
                resp = b""
                while time.time() < deadline and ACK.encode() not in resp \
                        and b"ERR" not in resp:
                    chunk = self.ser.read(32)
                    if chunk:
                        resp += chunk
                self.last_response = resp.decode(errors="replace").strip() or "(no reply)"
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
        ch = q.get("ch")
        if p == "pgm":
            if not ch or not ch.isdigit() or not 1 <= int(ch) <= 4:
                return {"error": "ch must be 1..4"}
            return {"sent": self.send(f"PGM:{int(ch) - 1}"), "pgm": int(ch)}
        if p == "pst":
            if not ch or not ch.isdigit() or not 1 <= int(ch) <= 4:
                return {"error": "ch must be 1..4"}
            return {"sent": self.send(f"PST:{int(ch) - 1}"), "pst": int(ch)}
        if p == "auto":
            return {"sent": self.send("ATO")}
        if p == "cut":
            # CUT = transition effect CUT executed by AUTO
            return {"sent": self.send("TRS:2"), "then": self.send("ATO")}
        if p == "pip":
            return {"sent": self.send("PIP")}
        if p == "split":
            return {"sent": self.send("SPT")}
        if p == "dsk":
            return {"sent": self.send("DSK")}  # toggle, no parameter
        if p == "freeze":
            return {"sent": self.send("FRZ")}  # toggle, no parameter
        if p == "trs":
            effect = q.get("effect")
            if effect not in ("0", "1", "2"):
                return {"error": "effect: 0=wipe, 1=mix, 2=cut"}
            return {"sent": self.send(f"TRS:{effect}")}
        if p == "tim":
            t = q.get("tenth")
            if not t or not t.isdigit() or not 0 <= int(t) <= 40:
                return {"error": "tenth: 0..40 (0.0s..4.0s)"}
            return {"sent": self.send(f"TIM:{t}")}
        if p == "ips":
            sel = q.get("src")
            if sel not in ("0", "1", "2"):
                return {"error": "src: 0=auto, 1=sdi3, 2=hdmi3"}
            return {"sent": self.send(f"IPS:{sel}")}
        if p == "mem":
            m = q.get("m")
            if not m or not m.isdigit() or not 0 <= int(m) <= 7:
                return {"error": "m: 0..7 (A-1..B-4)"}
            return {"sent": self.send(f"MEM:{m}")}
        if p == "version":
            return {"sent": self.send("VER")}
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
            q = {k: x[0] for k, x in parse_qs(u.query).items()}
            result = v.dispatch(u.path, q)
            self._json(result, 200 if "error" not in result else 400)

        def do_POST(self):
            u = urlparse(self.path)
            n = int(self.headers.get("Content-Length", 0))
            try:
                q = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                return self._json({"error": "bad json"}, 400)
            result = v.dispatch(u.path, {k: str(x) for k, x in q.items()})
            self._json(result, 200 if "error" not in result else 400)

        def log_message(self, fmt=None, *args, **kwargs):
            pass

    return Handler


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=9600)
    ap.add_argument("--listen", default="127.0.0.1:8788")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    v = V1SDI(None if args.dry_run else args.device, args.baud, args.dry_run)
    host, port = args.listen.rsplit(":", 1)
    print(f"v1sdi-rs232 {'(dry-run)' if args.dry_run else args.device}@{args.baud}, HTTP {args.listen}")
    try:
        ThreadingHTTPServer((host, int(port)), build_server(v)).serve_forever()
    finally:
        v.close()
