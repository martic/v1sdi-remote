# v1sdi-remote — Bitfocus Companion / Stream Deck control of the Roland V-1SDI

pbcc project. Companion (on the Raspberry Pi) drives the church's Roland
V-1SDI 3G-SDI switcher. Two control transports are available; this project
implements **RS-232 first** (documented, robust) with **USB MIDI** as phase 2.

## Why both work

The V-1SDI exposes two remote-control surfaces (Roland Reference Manual):

1. **RS-232 (DB-9)** — plain ASCII command set: `stx<NAME>:<param>;` sends,
   the unit answers `ack` / `err`. No SDK needed; a USB–RS232 adapter on the
   Pi is all the hardware required.
2. **USB-B MIDI** — the official V-1SDI RCS software runs over USB MIDI, so
   every panel action is a MIDI Note/CC. A Python daemon (rtmidi) can send the
   same messages; also lets Companion's MIDI module work directly.

## RS-232 wiring

- USB→RS-232 adapter (FTDI/CH340) on the Pi + DB-9 **null-modem (crossover)
  cable** to the V-1SDI's REMOTE (RS-232) connector
- If nothing responds, try a straight cable instead — Roland's docs specify
  DTE/DCE per model; null-modem is the usual requirement
- Serial settings: **38400 8N1, no flow control** (Roland Pro-AV default; also
  configurable in the unit's SETUP menu — confirm on the unit under RS-232)

## Verified RS-232 command set (from the V-1SDI Reference Manual)

| Function                        | Command        | Notes                       |
|---------------------------------|----------------|-----------------------------|
| Select PGM (bus A) channel      | `stxPGM:a;`    | a: 0–3 = CH1–CH4            |
| Select PST (bus B) channel      | `stxPST:a;`    | a: 0–3 = CH1–CH4            |
| Transition effect               | `stxTRS:a;`    | a: 0=WIPE 1=MIX 2=…         |
| AUTO (execute transition)       | `stxATO`       |                             |
| Cut (immediate switch)          | `stxCUT`       |                             |
| Input 3 source select           | `stxIPS:a;`    | a: 0=AUTO 1=SDI3 2=HDMI3    |
| INPUT4 scaling                  | `stxISC:a;`    | a: 0=FULL 1=LBOX 2=CROP 3=DOT|
| PVW connector output            | `stxOPS:a;`    | a: 0=MV 1=PST 2=PGM         |
| DSK key level                   | `stxKYL:a;`    | a: 0–255                    |
| Freeze                          | `stxFRZ:a;`    | (verify param in manual)    |
| Output fade                     | `stxOFA:a;`    | (verify param in manual)    |

Notes:
- Every command is ASCII terminated with `;`; `stx` is sent as the literal
  three characters. Device answers `ack` or `err`.
- Items marked *(verify)*: parameters need checking against the official
  "V-1SDI Reference Manual Ver.1.5" PDF (Roland support page → Owner's
  Manuals → Reference Manual). Fetch it in a browser (Roland's static CDN
  blocks scripted downloads) and drop it in `docs/`.
- CUT vs AUTO: CUT swaps immediately, AUTO runs the transition effect set by
  `stxTRS` — for church song-slide switching, bind PGM/PST to buttons and
  AUTO/CUT to two others.

## Phase 2 — USB MIDI

Plug the V-1SDI's USB-B port into the Pi; `rtmidi` daemon mode sends the same
actions as MIDI. Map the Note/CC numbers from the Reference Manual's MIDI
Implementation section (need the PDF — blocked from scripted download, fetch
in browser). Benefit: one USB cable, and Companion's generic MIDI module can
also hit it without our daemon.

## Software — `v1sdi_rs232.py`

Same pattern as LancBridge: HTTP on 127.0.0.1:8788, GET endpoints for
Companion:

| Stream Deck button   | GET endpoint                     |
|----------------------|----------------------------------|
| PGM 1–4              | `/pgm?ch=1..4`                   |
| PST 1–4              | `/pst?ch=1..4`                   |
| AUTO (take)          | `/auto`                          |
| CUT                  | `/cut`                           |
| Transition wipe/mix  | `/trs?effect=0|1`                |
| Freeze on/off        | `/freeze?state=on|off`           |
| DSK on/off           | `/dsk?state=on|off`              |
| Fade to black/white  | `/fade?color=black|white&state=` |
| Status               | `/status` (last ack/err, current pgm) |

`--device /dev/ttyUSB0 --baud 38400`; `--dry-run` logs commands without a
serial port (for building the Companion config on the X1).

## Deployment

`v1sdi-rs232.service` for the Pi, same routine as LancBridge. Stream Deck →
Companion → HTTP Request → daemon → USB-RS232 → V-1SDI.

## Status

- [x] Command set extracted from Roland documentation (RS-232)
- [ ] Official Reference Manual PDF into `docs/` (fetch in a browser — CDN
      blocks scripted fetches)
- [ ] MIDI note/CC map (phase 2, same PDF)
- [ ] Hardware verification on the church unit
