# AIS-APRS for OpenCPN

*[Leer en español](README.es.md)*

A small desktop app (Python + Tkinter) that adds two internet services to [OpenCPN](https://opencpn.org), using the GPS data that OpenCPN itself sends over the network:

- **Internet AIS**: downloads live AIS targets near the vessel from [aisstream.io](https://aisstream.io) and feeds them to OpenCPN as standard NMEA `!AIVDM` sentences, so they appear on the chart like targets from a normal receiver.
- **APRS position reporting**: publishes the vessel's position (with course and speed) to the APRS-IS network, so it can be followed on [aprs.fi](https://aprs.fi) and similar sites. *Requires an amateur radio licence.*

It is meant for boats that only have an AIS receiver (or none) and want to see traffic beyond the range of their antenna, and to be followed from ashore.

> ⚠️ Internet AIS has variable delays and only shows vessels picked up by a shore station in the aisstream network. **It is not a replacement for an AIS receiver or a proper lookout, and must never be used for collision avoidance.**

## Features

- Internet AIS area that **re-centres automatically** as the vessel moves.
- **VHF priority**: if your own AIS receiver feeds OpenCPN, any vessel heard on the radio is ignored from the internet for a configurable time (5 min by default). Targets do not jump between the real-time and the delayed position.
- **Stale data filter**: internet reports older than a configurable age (120 s by default) are dropped. Their age is measured against GPS time, so a wrong PC clock does not matter.
- **Internet targets are marked**: OpenCPN cannot colour targets by source, so their names get a suffix, `NEPTUNE THELIS (NET)` by default.
- **APRS beacons**: at a set interval when moving and when stopped, plus an extra beacon on large course changes. Nothing is sent without a recent GPS fix.
- **Interface in English and Spanish**, selectable in the app (automatic by default).
- Built-in **test vessel**, to check the OpenCPN connection.

## Files

| File | What it is |
|---|---|
| `opencpn_net.pyw` | The windowed app (AIS + APRS). Opens with a double click, without a console. |
| `aisstream_opencpn.py` | AIS encoder and internet/VHF merging filter. The app needs it in the same folder. It also works on its own from the command line. |
| `i18n.py` | Translations (English / Spanish). |

## Installation

1. Install [Python 3](https://www.python.org/downloads/) (on Windows, tick *Add python.exe to PATH*).
2. Install the only dependency (needed for AIS only):
   ```
   pip install -r requirements.txt
   ```
3. Put the three `.py`/`.pyw` files in the same folder.
4. Get a free API key at [aisstream.io](https://aisstream.io) (sign in with a GitHub account).

## OpenCPN setup

In *Options → Connections → Add connection*:

| Connection | Type | Address | Port | Direction |
|---|---|---|---|---|
| AIS input | Network, UDP | `127.0.0.1` | `10111` | Receive input |
| Data output | Network, UDP | `127.0.0.1` | `10112` | Output on this port, output filter **RMC** and **VDM** |

- **RMC** provides the position, used to centre the AIS area and for APRS.
- **VDM** forwards the AIS targets received by your own VHF, and is only needed for **VHF priority**. For that, your AIS receiver must already be an input in OpenCPN, for example through a serial NMEA 0183 port or an NMEA 2000 gateway. The app recognises and ignores its own internet data if OpenCPN echoes it back.

## Usage

Open `opencpn_net.pyw`. It has four tabs:

- **Status**: GPS position, AIS and APRS status, traffic received (with the number of reports dropped as stale or heard on VHF), last beacon and a message log. It has buttons to start and stop each service and to *Send position now*.
- **AIS**: API key, radius of the area in NM, output IP and port to OpenCPN, MMSI to ignore (your own), maximum report age, VHF priority time, name suffix for internet targets, and the test vessel.
- **APRS**: callsign with SSID (`-8` is usual for boats), APRS-IS passcode (with a button to calculate it), server, symbol (`/Y` sailboat, `/s` ship), comment and beacon intervals.
- **General**: language, data port, initial position (used until GPS data arrives), OpenCPN path, and an option to launch OpenCPN at start-up.

If you tick *Start … automatically* and *Open OpenCPN when this app starts*, a single shortcut starts everything. If a double click runs a different Python installation without `websockets`, create a shortcut that points to the right one explicitly:

```
"C:\...\Python313\pythonw.exe" "C:\...\opencpn_net.pyw"
```

Settings are saved in `opencpn_net_config.json`, next to the app. That file contains your API key and passcode, so it is excluded from the repository (`.gitignore`). Settings from version 1.0 are imported automatically.

### APRS beacons

Example packet:

```
N0CALL-8>APRS,TCPIP*:!3636.00N/00618.00WY200/005OpenCPN AIS-APRS
```

It contains the position, the symbol, the course over ground and speed in knots (only when moving, ≥ 0.5 kn) and the comment.

- Moving (≥ 1 kn): every *N* minutes (5 by default), plus an extra beacon when the course changes ≥ 30° (at most once a minute).
- Stopped: every 30 minutes.
- Nothing is sent if there has been no GPS data for 2 minutes.
- The minimum intervals are 1 min when moving and 5 min when stopped, so as not to flood the APRS network.

## Command line (AIS only)

```
python aisstream_opencpn.py --key YOUR_API_KEY --lat 36.6 --lon -6.3 --radius 30 --gps-port 10112
```

Useful options: `--max-age`, `--rf-hold`, `--tag`, `--diag` (prints every vessel received and its distance), `--test` (fake test vessel), `-v` (prints every NMEA sentence), `--lang es|en`. See `--help`.

## Supported AIS message types

1, 2 and 3 (class A position), 5 (class A static data), 18 and 19 (class B position) and 24 (class B static data). The AIS encoder has no external dependencies.

## Related projects

- [AisLive_pi](https://github.com/Kupofty/AisLive_pi): an OpenCPN plugin that shows internet AIS from OpenWaters.io.

## Licence

Copyright (C) 2026 EA7KE. Distributed under the [GNU General Public License v2.0 or later](LICENSE) (GPL-2.0-or-later), the same licence family as OpenCPN.
