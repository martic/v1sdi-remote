# v1sdi-remote — Bitfocus Companion / Stream Deck control of the Roland V-1SDI

pbcc project. Companion (on the Raspberry Pi) drives the church's Roland
V-1SDI 3G-SDI switcher over the **USB-B cable only** (USB MIDI + Roland
SysEx), verified against the official Reference Manual
(`docs/V-1SDI_reference_v15_eng02_W.pdf`). No RS-232 needed.

## Protocol facts (from the Reference Manual — verified)

The USB-B port is a USB-MIDI endpoint (this is what the official V-1SDI RCS
Windows/Mac software speaks). Two message layers:

1. **Channel messages** — CC18 = A/B fader (video transition), CC19/20 =
   transition time/effect, CC26 = output fade, CC28/29 = CONTROL knobs,
   CC10–16 = audio mixer levels, Bank Select + Program Change = input select
   (bus A/B, memory 1–8). There are **no Note messages**.
2. **Roland SysEx (RQ1/DT1)** — the full Parameter Address Map (~316
   addresses) covering every menu parameter, plus read-only LED/meter status
   for tally feedback.

Framing:

- **DT1 (write)**: `F0 41 10 00 00 00 31 12 <addr3> <data..> <sum> F7`
- **RQ1 (read)**:  `F0 41 10 00 00 00 31 11 <addr3> <size3> <sum> F7`
- `41` = Roland manufacturer ID, `10` = device ID, `00 00 00 31` = V-1SDI
  model ID; `sum` = Roland 7-bit checksum of addr+data
- Requires V-1SDI system program **Ver.1.5+**

## Parameter addresses used

| Function                | SysEx address | Values                              |
|-------------------------|---------------|-------------------------------------|
| PGM (bus A) input       | `71 03 08`    | 0–3 = Input 1–4                     |
| PST (bus B) input       | `71 03 09`    | 0–3 = Input 1–4                     |
| [PinP] button           | `71 03 0A`    | 0/1                                 |
| [SPLIT] button          | `71 03 0B`    | 0/1                                 |
| Transition pattern      | `71 03 0C`    | 0=WIPE 1=MIX 2=CUT                  |
| [DSK] button            | `71 03 0D`    | 0/1                                 |
| Memory select           | `73 01 00`    | 0–7 = MEMORY 1–8                    |
| [FREEZE] button         | `73 04 00`    | 00=off 20=long-press 40=on          |
| A/B fader (transition)  | CC18          | 0 = bus A end … 127 = bus B end     |
| Tally / status (read)   | `73 02 xx`    | LED states, audio meters (via RQ1)  |

**AUTO / CUT emulation**: set the transition pattern, then move the A/B fader
(CC18) — jump 0→127 with CUT for an instant cut, ramp for a timed transition.

(The full ~316-address map is in the PDF, "3. Parameter Address Map" — the
daemon exposes the table above; extending it is one line per address.)

## Daemon — `v1sdi_midi.py`

HTTP on `127.0.0.1:8789` (GET for Companion's HTTP Request module; POST also
supported):

| Stream Deck button       | GET endpoint                    |
|--------------------------|---------------------------------|
| PGM 1–4                  | `/pgm?ch=1..4`                  |
| PST 1–4                  | `/pst?ch=1..4`                  |
| AUTO (timed take)        | `/auto`                         |
| CUT (instant)            | `/cut`                          |
| Transition wipe/mix/cut  | `/trs?effect=0|1|2`             |
| PinP / SPLIT             | `/pip`, `/split`                |
| DSK toggle               | `/dsk`                          |
| Freeze                   | `/freeze`                       |
| Memory 1–8               | `/mem?m=0..7`                   |
| Status / tally           | `/status`                       |

Run on the Pi:

```
sudo apt install -y python3-dev libasound2-dev
sudo pip3 install python-rtmidi
python3 v1sdi_midi.py --port-name V-1SDI
```

`--port-name` matches a substring of the ALSA MIDI port name (the port the
V-1SDI enumerates as); omit it to use a virtual port for testing.
`--dry-run` runs the full HTTP API without a device.

## Deployment (Pi, systemd)

```
sudo cp v1sdi_midi.py /opt/v1sdi-remote/
sudo cp v1sdi-midi.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now v1sdi-midi
```

`Restart=on-failure` retries every 3 s, so boot order vs. USB enumeration
doesn't matter. Verify: `curl http://127.0.0.1:8789/status` →
`"dry-run": false` plus state means live.

Chain: Stream Deck (USB) → Companion (Pi) → v1sdi_midi.py → USB-B → V-1SDI.

## Status

- [x] Protocol verified against official Reference Manual (PDF in `docs/`)
- [x] SysEx framing + checksum validated in tests
- [x] Daemon HTTP API tested end-to-end
- [ ] Hardware verification on the church unit
