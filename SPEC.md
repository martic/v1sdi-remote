# v1sdi-remote — Bitfocus Companion / Stream Deck control of the Roland V-1SDI

pbcc project. Companion (on the Raspberry Pi) drives the church's Roland
V-1SDI 3G-SDI switcher. RS-232 is implemented and verified against the
official Reference Manual (`docs/V-1SDI_reference_v15_eng02_W.pdf`);
USB MIDI is documented here as phase 2.

## RS-232 facts (from the Reference Manual — verified)

- **Framing**: each command is `STX (0x02)` + 3 uppercase letters + optional
  `:param` + `;`. The unit replies `ACK (0x06)` or `stxERR:a;`
  (a: 0=syntax error …). **Wait for ACK before sending the next command.**
- **Serial**: 9600 bps, 8 data bits, no parity, 1 stop bit, ASCII, **XON/XOFF**
  flow control.
- **Cable**: V-1SDI is DTE — pin 2 = RXD, pin 3 = TXD, pin 5 = GND (pins 4–6
  and 7–8 are crossed internally). Use a **crossover (null-modem) cable** to
  the Pi's USB-RS232 adapter.
- Commands are case-sensitive uppercase; multiple args are comma-separated.

## Command set (all verified from the manual)

| Function                          | Command      | Param                                            |
|-----------------------------------|--------------|--------------------------------------------------|
| Select PGM (bus A) channel        | `PGM:a`      | a: 0–3 = CH1–4                                   |
| Select PST (bus B) channel        | `PST:a`      | a: 0–3 = CH1–4                                   |
| Transition effect                 | `TRS:a`      | a: 0=WIPE 1=MIX 2=CUT                            |
| Transition time                   | `TIM:a`      | a: 0–40 (0.0–4.0 s)                              |
| Press [AUTO] (execute transition) | `ATO`        | —                                                |
| Press [PinP]                      | `PIP`        | —                                                |
| Press [SPLIT]                     | `SPT`        | —                                                |
| Press [DSK] (toggle)              | `DSK`        | —                                                |
| Press [FREEZE] (toggle)           | `FRZ`        | —                                                |
| INPUT 3 source                    | `IPS:a`      | a: 0=AUTO 1=SDI3 2=HDMI3                         |
| INPUT 4 scaling                   | `ISC:a`      | a: 0=FULL 1=LETTERBOX 2=CROP 3=DOT-BY-DOT        |
| PVW (SDI) output assign           | `OPS:a`      | a: 0=MULTI-VIEW 1=PST 2=PGM                      |
| MULTI-VIEW (HDMI) output assign   | `OMS:a`      | a: 0=MULTI-VIEW 1=PST 2=PGM                      |
| DSK key level                     | `KYL:a`      | a: 0–255                                         |
| Call memory                       | `MEM:a`      | a: 0–7 (A-1..B-4)                                |
| Version                           | `VER`        | replies `stxVER:V-1SDI,<ver>;`                   |
| Status query                      | `QPL:a`      | a: 0=PGM 1=PST 2=PinP … (see manual p.10)        |
| Input connector of INPUT 3 (q)    | `QIS:a;`     | replies `a: 0 (SDI), 1 (HDMI)`                   |

Also available (not exposed over HTTP yet): INPUT-4 position/zoom (`IHP/IVP/
IZM`), PinP/SPLIT position (`PQA…SVB`), audio levels (`IAL/OAL/ADT/QAL`),
HDCP (`HCP`), auto scan (`ASN`).

**CUT** isn't a separate command — set `TRS:2` then `ATO`.

## MIDI facts (USB-B port, phase 2)

Channel fixed at 1. **No Note messages** — buttons aren't MIDI-mapped. What
MIDI does cover:

- **Input select**: Bank Select (CC0 = MSB, CC32 = LSB) then Program Change:
  - MSB 00 / LSB 00, PC 00–03 → bus A input 1–4
  - MSB 01 / LSB 00, PC 00–03 → bus B input 1–4
  - MSB 50 / LSB 00, PC 00–07 → MEMORY 1–8
- **CC18** (12H): A/B fader position (video transition)
- **CC19** (13H): transition time
- **CC20** (14H): transition effect type
- **CC26**: OUTPUT FADE knob state
- **CC28 / CC29**: CONTROL 1 / CONTROL 2 knobs
- **CC10–CC16**: audio mixer levels (SDI1, SDI2, SDI3, HDMI3, HDMI4, MASTER …)
- Buttons (AUTO, DSK, FREEZE, PinP, SPLIT) are **RS-232 only** — one more
  reason the RS-232 route is the primary control surface.

For deeper control (panel state etc.) the manual defines Roland SysEx RQ1/DT1
(model ID 00 00 00 31, device ID 10H) with a parameter address map (p.14+).

## Daemon — `v1sdi_rs232.py`

HTTP on 127.0.0.1:8788 (GET for Companion HTTP Request, POST also supported):

| Stream Deck button       | GET endpoint                          |
|--------------------------|---------------------------------------|
| PGM 1–4                  | `/pgm?ch=1..4`                        |
| PST 1–4                  | `/pst?ch=1..4`                        |
| AUTO (take)              | `/auto`                               |
| CUT (immediate)          | `/cut` (sends TRS:2 then ATO)         |
| Wipe / Mix / Cut preset  | `/trs?effect=0|1|2`                   |
| Transition time          | `/tim?tenth=0..40`                    |
| PinP / SPLIT             | `/pip`, `/split`                      |
| DSK toggle               | `/dsk`                                |
| Freeze toggle            | `/freeze`                             |
| Memory 1–8               | `/mem?m=0..7`                         |
| INPUT3 SDI/HDMI/auto     | `/ips?src=1|2|0`                      |
| Status                   | `/status` (pgm/pst, last ack/err)     |

Run on the Pi: `python3 v1sdi_rs232.py --device /dev/ttyUSB0` (9600 default);
`--dry-run` for building the Companion config without the cable.
`v1sdi-rs232.service` runs it at boot.

## Companion wiring (Stream Deck)

- HTTP Request module → `http://127.0.0.1:8788/...` per table above
- Song-slide workflow: 4 PGM buttons, 4 PST buttons, one AUTO (take)
- `/status` polled into a variable for PGM-number feedback on buttons

## Status

- [x] RS-232 protocol verified against official Reference Manual
- [x] MIDI map extracted (bank/PC + CC list above)
- [x] Daemon tested end-to-end against a stubbed serial port
- [ ] Hardware verification on the church unit (cable crossover + 9600)
- [ ] Phase 2: MIDI daemon (`rtmidi`) or SysEx DT1 for full parameter control
