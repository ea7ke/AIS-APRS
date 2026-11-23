#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EA7KE
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License, version 2 or (at your option)
# any later version. It is distributed WITHOUT ANY WARRANTY. See LICENSE.
"""
aisstream.io -> OpenCPN
=======================
Downloads live AIS data from aisstream.io for an area around the vessel,
converts it to NMEA !AIVDM sentences and sends them to OpenCPN over UDP.

This file is both:
  * a library used by the GUI app (opencpn_net.pyw): AIS encoder, NMEA parsing
    and the internet/VHF merging filter (NetFilter), and
  * a command-line tool.

Requirements:
    pip install websockets

Command line (fixed area):
    python aisstream_opencpn.py --key YOUR_API_KEY --lat 36.6 --lon -6.3

Following the vessel (OpenCPN sends RMC, and optionally VDM, over UDP):
    python aisstream_opencpn.py --key YOUR_API_KEY --lat 36.6 --lon -6.3 --gps-port 10112

OpenCPN setup (Options -> Connections -> Add connection):
  1) AIS input:   Network, UDP, 127.0.0.1, port 10111, "Receive input".
  2) Data output: Network, UDP, 127.0.0.1, port 10112, "Output on this port",
                  output filter RMC (position) and VDM (VHF AIS, for VHF priority).
"""

import argparse
import asyncio
import datetime as dt
import json
import math
import socket
import ssl
import time

from i18n import t, set_language

AISSTREAM_URL = "wss://stream.aisstream.io/v0/stream"
MESSAGE_TYPES = [
    "PositionReport",                 # types 1/2/3 (class A position)
    "StandardClassBPositionReport",   # type 18 (class B position)
    "ExtendedClassBPositionReport",   # type 19 (class B position + static)
    "ShipStaticData",                 # type 5 (class A name, destination...)
    "StaticDataReport",               # type 24 (class B name, callsign...)
]
TEST_MMSI = 999000001

# --------------------------------------------------------------------------
# AIS encoder -> NMEA (no external dependencies)
# --------------------------------------------------------------------------


class Bits:
    """Bit buffer for building an AIS message payload."""

    def __init__(self):
        self.b = []

    def u(self, value, n):
        """Unsigned integer of n bits."""
        value = int(value) & ((1 << n) - 1)
        self.b.extend((value >> (n - 1 - i)) & 1 for i in range(n))

    def s(self, value, n):
        """Signed integer of n bits (two's complement)."""
        self.u(int(value) & ((1 << n) - 1), n)

    def t(self, text, nchars):
        """6-bit ASCII text, padded with '@'."""
        text = (text or "").upper()[:nchars].ljust(nchars, "@")
        for ch in text:
            c = ord(ch)
            if 64 <= c <= 95:
                v = c - 64
            elif 32 <= c <= 63:
                v = c
            else:
                v = 0  # invalid character -> '@'
            self.u(v, 6)

    def payload(self):
        """Armoured payload string and number of fill bits."""
        fill = (6 - len(self.b) % 6) % 6
        bits = self.b + [0] * fill
        out = []
        for i in range(0, len(bits), 6):
            v = 0
            for bit in bits[i:i + 6]:
                v = (v << 1) | bit
            out.append(chr(v + 48 if v < 40 else v + 56))
        return "".join(out), fill


_seq = 0


def nmea_sentences(bits, channel="A"):
    """Return the list of !AIVDM sentences (split into fragments if needed)."""
    global _seq
    payload, fill = bits.payload()
    parts = [payload[i:i + 60] for i in range(0, len(payload), 60)] or [""]
    total = len(parts)
    seq = ""
    if total > 1:
        seq = str(_seq)
        _seq = (_seq + 1) % 10
    sentences = []
    for i, part in enumerate(parts, 1):
        f = fill if i == total else 0
        body = f"AIVDM,{total},{i},{seq},{channel},{part},{f}"
        cs = 0
        for ch in body:
            cs ^= ord(ch)
        sentences.append(f"!{body}*{cs:02X}")
    return sentences


def _lonlat(b, lon, lat):
    b.s(round((lon if lon is not None else 181) * 600000), 28)
    b.s(round((lat if lat is not None else 91) * 600000), 27)


def _sog(v):
    return 1023 if v is None else min(int(round(v * 10)), 1022)


def _cog(v):
    return 3600 if v is None or v >= 360 else int(round(v * 10))


def _hdg(v):
    return 511 if v is None or v > 359 else int(v)


def _dims(b, d):
    d = d or {}
    b.u(min(d.get("A", 0), 511), 9)
    b.u(min(d.get("B", 0), 511), 9)
    b.u(min(d.get("C", 0), 63), 6)
    b.u(min(d.get("D", 0), 63), 6)


def encode_type1(mmsi, m):
    """Class A position report (aisstream 'PositionReport')."""
    b = Bits()
    b.u(1, 6); b.u(0, 2); b.u(mmsi, 30)
    b.u(m.get("NavigationalStatus", 15), 4)
    rot = m.get("RateOfTurn", -128)
    b.s(max(-128, min(127, int(rot))), 8)
    b.u(_sog(m.get("Sog")), 10)
    b.u(1 if m.get("PositionAccuracy") else 0, 1)
    _lonlat(b, m.get("Longitude"), m.get("Latitude"))
    b.u(_cog(m.get("Cog")), 12)
    b.u(_hdg(m.get("TrueHeading")), 9)
    b.u(m.get("Timestamp", 60), 6)
    b.u(m.get("SpecialManoeuvreIndicator", 0), 2)
    b.u(0, 3)
    b.u(1 if m.get("Raim") else 0, 1)
    b.u(0, 19)
    return b


def encode_type18(mmsi, m):
    """Class B position report (aisstream 'StandardClassBPositionReport')."""
    b = Bits()
    b.u(18, 6); b.u(0, 2); b.u(mmsi, 30); b.u(0, 8)
    b.u(_sog(m.get("Sog")), 10)
    b.u(1 if m.get("PositionAccuracy") else 0, 1)
    _lonlat(b, m.get("Longitude"), m.get("Latitude"))
    b.u(_cog(m.get("Cog")), 12)
    b.u(_hdg(m.get("TrueHeading")), 9)
    b.u(m.get("Timestamp", 60), 6)
    b.u(0, 2)
    b.u(1 if m.get("ClassBUnit", True) else 0, 1)
    b.u(1 if m.get("ClassBDisplay") else 0, 1)
    b.u(1 if m.get("ClassBDsc") else 0, 1)
    b.u(1 if m.get("ClassBBand", True) else 0, 1)
    b.u(1 if m.get("ClassBMsg22") else 0, 1)
    b.u(1 if m.get("AssignedMode") else 0, 1)
    b.u(1 if m.get("Raim") else 0, 1)
    b.u(0, 20)
    return b


def encode_type19(mmsi, m):
    """Extended class B report (aisstream 'ExtendedClassBPositionReport')."""
    b = Bits()
    b.u(19, 6); b.u(0, 2); b.u(mmsi, 30); b.u(0, 8)
    b.u(_sog(m.get("Sog")), 10)
    b.u(1 if m.get("PositionAccuracy") else 0, 1)
    _lonlat(b, m.get("Longitude"), m.get("Latitude"))
    b.u(_cog(m.get("Cog")), 12)
    b.u(_hdg(m.get("TrueHeading")), 9)
    b.u(m.get("Timestamp", 60), 6)
    b.u(0, 4)
    b.t(m.get("Name", ""), 20)
    b.u(m.get("Type", 0), 8)
    _dims(b, m.get("Dimension"))
    b.u(m.get("FixType", 0), 4)
    b.u(1 if m.get("Raim") else 0, 1)
    b.u(1 if m.get("Dte", True) else 0, 1)
    b.u(1 if m.get("AssignedMode") else 0, 1)
    b.u(0, 4)
    return b


def encode_type5(mmsi, m):
    """Class A static and voyage data (aisstream 'ShipStaticData')."""
    b = Bits()
    b.u(5, 6); b.u(0, 2); b.u(mmsi, 30)
    b.u(m.get("AisVersion", 0), 2)
    b.u(m.get("ImoNumber", 0), 30)
    b.t(m.get("CallSign", ""), 7)
    b.t(m.get("Name", ""), 20)
    b.u(m.get("Type", 0), 8)
    _dims(b, m.get("Dimension"))
    b.u(m.get("FixType", 0), 4)
    eta = m.get("Eta") or {}
    b.u(eta.get("Month", 0), 4)
    b.u(eta.get("Day", 0), 5)
    b.u(eta.get("Hour", 24), 5)
    b.u(eta.get("Minute", 60), 6)
    b.u(min(int(round((m.get("MaximumStaticDraught") or 0) * 10)), 255), 8)
    b.t(m.get("Destination", ""), 20)
    b.u(1 if m.get("Dte", True) else 0, 1)
    b.u(0, 1)
    return b


def encode_type24(mmsi, m):
    """Class B static data, parts A and/or B (aisstream 'StaticDataReport')."""
    out = []
    ra, rb = m.get("ReportA") or {}, m.get("ReportB") or {}
    if ra.get("Valid"):
        b = Bits()
        b.u(24, 6); b.u(0, 2); b.u(mmsi, 30); b.u(0, 2)
        b.t(ra.get("Name", ""), 20)
        out.append(b)
    if rb.get("Valid"):
        b = Bits()
        b.u(24, 6); b.u(0, 2); b.u(mmsi, 30); b.u(1, 2)
        b.u(rb.get("ShipType", 0), 8)
        b.t(rb.get("VendorIDName", ""), 3)
        b.u(rb.get("VenderIDModel", 0), 4)
        b.u(rb.get("VenderIDSerial", 0), 20)
        b.t(rb.get("CallSign", ""), 7)
        _dims(b, rb.get("Dimension"))
        b.u(0, 6)
        out.append(b)
    return out


def message_body(msg):
    """(type name, body dict, MMSI) of an aisstream JSON message."""
    kind = msg.get("MessageType")
    body = (msg.get("Message") or {}).get(kind) or {}
    mmsi = body.get("UserID") or (msg.get("MetaData") or {}).get("MMSI")
    return kind, body, mmsi


def convert(msg):
    """aisstream JSON message -> list of NMEA sentences."""
    kind, body, mmsi = message_body(msg)
    if not mmsi:
        return []
    if kind == "PositionReport":
        blocks = [encode_type1(mmsi, body)]
    elif kind == "StandardClassBPositionReport":
        blocks = [encode_type18(mmsi, body)]
    elif kind == "ExtendedClassBPositionReport":
        blocks = [encode_type19(mmsi, body)]
    elif kind == "ShipStaticData":
        blocks = [encode_type5(mmsi, body)]
    elif kind == "StaticDataReport":
        blocks = encode_type24(mmsi, body)
    else:
        return []
    sentences = []
    for b in blocks:
        sentences.extend(nmea_sentences(b))
    return sentences


def test_vessel_sentences(lat, lon):
    """Fake vessel 1 NM north of (lat, lon), to check that OpenCPN receives data."""
    m = {"Latitude": lat + 1 / 60, "Longitude": lon, "Sog": 0, "Cog": 0,
         "TrueHeading": 0, "NavigationalStatus": 1, "Timestamp": int(time.time()) % 60}
    out = nmea_sentences(encode_type1(TEST_MMSI, m))
    out += nmea_sentences(encode_type5(TEST_MMSI, {"Name": "TEST TARGET", "Type": 37}))
    return out


# --------------------------------------------------------------------------
# NMEA input parsing (from OpenCPN)
# --------------------------------------------------------------------------

def parse_rmc(line):
    """RMC sentence -> (lat, lon, sog_kn, cog or None, utc datetime or None), or None."""
    try:
        if not line.startswith("$"):
            return None
        c = line.split("*")[0].split(",")
        if not c[0].endswith("RMC") or c[2] != "A":
            return None
        lat = int(c[3][:2]) + float(c[3][2:]) / 60
        if c[4] == "S":
            lat = -lat
        lon = int(c[5][:3]) + float(c[5][3:]) / 60
        if c[6] == "W":
            lon = -lon
        sog = float(c[7]) if c[7] else 0.0
        cog = float(c[8]) if c[8] else None
        utc = None
        if len(c[1]) >= 6 and len(c[9]) == 6:
            utc = dt.datetime(2000 + int(c[9][4:6]), int(c[9][2:4]), int(c[9][0:2]),
                              int(c[1][0:2]), int(c[1][2:4]), int(c[1][4:6]),
                              tzinfo=dt.timezone.utc)
        return lat, lon, sog, cog, utc
    except (IndexError, ValueError):
        return None


def vdm_mmsi(line):
    """MMSI of the first fragment of a !xxVDM sentence, or None.

    Returns (mmsi, payload). Own-vessel sentences (VDO) are ignored.
    """
    try:
        if not line.startswith("!") or line[3:6] != "VDM":
            return None
        f = line.split("*")[0].split(",")
        if f[2] != "1":           # only the first fragment carries the MMSI
            return None
        payload = f[5]
        if len(payload) < 7:
            return None
        bits = 0
        for ch in payload[:7]:    # 42 bits are enough for type + repeat + MMSI
            v = ord(ch) - 48
            if v > 40:
                v -= 8
            bits = (bits << 6) | v
        mmsi = (bits >> 4) & ((1 << 30) - 1)
        return mmsi, payload
    except (IndexError, ValueError):
        return None


def parse_aisstream_time(meta):
    """UTC datetime from aisstream MetaData.time_utc, or None."""
    s = (meta or {}).get("time_utc") or ""
    try:
        return dt.datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=dt.timezone.utc)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Merging internet AIS with local VHF AIS
# --------------------------------------------------------------------------

class NetFilter:
    """Decides which internet AIS reports reach OpenCPN, and how.

    * VHF priority: an MMSI heard on the local VHF receiver within the last
      `rf_hold` seconds is not forwarded from the internet, so targets do not
      jump between the real-time and the delayed position.
    * Stale data: reports older than `max_age` seconds are dropped. The age is
      measured against GPS time when available (the PC clock may be wrong).
    * Marking: vessel names from the internet get the suffix `tag`, and a
      synthetic static message is sent for vessels with no static data yet, so
      that every internet target shows the mark in OpenCPN.
    """

    STATIC_EVERY = 360        # seconds between synthetic name messages

    def __init__(self, max_age=120, rf_hold=300, tag=" (NET)", exclude=()):
        self.max_age = max_age
        self.rf_hold = rf_hold
        self.tag = tag or ""
        self.exclude = set(exclude)
        self.rf_seen = {}         # MMSI -> last time heard on VHF
        self.sent = {}            # payload -> time sent (to ignore our own echo)
        self.real_static = set()  # MMSIs with real static data from aisstream
        self.static_sent = {}     # MMSI -> time of last synthetic name
        self.clock_offset = 0.0   # GPS time minus PC time (seconds)
        self.rf_active = False
        self.stats = {"sent": 0, "old": 0, "rf": 0}
        self.vessels = set()

    # ---- inputs from OpenCPN
    def set_gps_time(self, utc):
        if utc is not None:
            self.clock_offset = utc.timestamp() - time.time()

    def feed_vdm(self, line):
        """Register a VDM sentence received from OpenCPN. True if it is VHF data."""
        r = vdm_mmsi(line)
        if not r:
            return False
        mmsi, payload = r
        now = time.time()
        if payload in self.sent and now - self.sent[payload] < 120:
            return False          # our own internet data echoed back by OpenCPN
        if mmsi == TEST_MMSI:
            return False
        self.rf_seen[mmsi] = now
        first = not self.rf_active
        self.rf_active = True
        return first

    # ---- processing of internet data
    def _tagged(self, name):
        name = (name or "").strip().rstrip("@").strip()
        if not self.tag or not name:
            return name
        return name[:20 - len(self.tag)].rstrip() + self.tag

    def process(self, msg):
        """aisstream message -> sentences to send (empty list if dropped)."""
        kind, body, mmsi = message_body(msg)
        meta = msg.get("MetaData") or {}
        if not mmsi or mmsi in self.exclude:
            return []
        now = time.time()
        # VHF priority
        heard = self.rf_seen.get(mmsi)
        if heard and self.rf_hold and now - heard < self.rf_hold:
            self.stats["rf"] += 1
            return []
        # stale data
        when = parse_aisstream_time(meta)
        if self.max_age and when is not None:
            age = now + self.clock_offset - when.timestamp()
            if age > self.max_age:
                self.stats["old"] += 1
                return []
        # mark the names
        msg = json.loads(json.dumps(msg))  # copy, do not modify the original
        kind, body, mmsi = message_body(msg)
        if kind in ("ShipStaticData", "ExtendedClassBPositionReport"):
            body["Name"] = self._tagged(body.get("Name"))
            self.real_static.add(mmsi)
        elif kind == "StaticDataReport" and (body.get("ReportA") or {}).get("Valid"):
            body["ReportA"]["Name"] = self._tagged(body["ReportA"].get("Name"))
            self.real_static.add(mmsi)
        out = convert(msg)
        # synthetic name for vessels without static data yet
        name = (meta.get("ShipName") or "").strip()
        if (self.tag and name and mmsi not in self.real_static
                and now - self.static_sent.get(mmsi, 0) > self.STATIC_EVERY):
            if kind == "PositionReport":       # class A -> type 5
                out += nmea_sentences(encode_type5(mmsi, {"Name": self._tagged(name)}))
            elif kind.startswith("StandardClassB"):  # class B -> type 24A
                out += nmea_sentences(encode_type24(
                    mmsi, {"ReportA": {"Valid": True, "Name": self._tagged(name)}})[0])
            self.static_sent[mmsi] = now
        for s in out:
            self.sent[s.split(",")[5]] = now
        self.stats["sent"] += len(out)
        self.vessels.add(mmsi)
        return out

    def take_stats(self):
        """Statistics since the last call, and clean-up of old entries."""
        s = dict(self.stats, vessels=len(self.vessels))
        self.stats = {"sent": 0, "old": 0, "rf": 0}
        self.vessels = set()
        now = time.time()
        self.sent = {p: tm for p, tm in self.sent.items() if now - tm < 120}
        self.rf_seen = {m: tm for m, tm in self.rf_seen.items() if now - tm < 3600}
        return s



def ssl_context():
    """TLS context for aisstream: full certificate verification, but without
    the extra X.509 strictness enabled by default since Python 3.13.

    Antivirus HTTPS scanning, mobile operators or other network middleboxes
    often present a CA certificate without an Authority Key Identifier, which
    Python 3.13+ rejects ("Missing Authority Key Identifier"). The chain and
    host name are still fully verified against the system trust store.
    """
    ctx = ssl.create_default_context()
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return ctx


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------

def bbox(lat, lon, radius_nm):
    """aisstream bounding box [[lat, lon], [lat, lon]] around a point."""
    dlat = radius_nm / 60.0
    dlon = radius_nm / (60.0 * max(math.cos(math.radians(lat)), 0.01))
    return [[lat - dlat, lon - dlon], [lat + dlat, lon + dlon]]


def distance_nm(lat1, lon1, lat2, lon2):
    dlat = (lat2 - lat1) * 60
    dlon = (lon2 - lon1) * 60 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dlat, dlon)


# --------------------------------------------------------------------------
# Command-line tool
# --------------------------------------------------------------------------

class _State:
    def __init__(self, lat, lon, radius):
        self.lat, self.lon, self.radius = lat, lon, radius
        self.sub_lat, self.sub_lon = lat, lon
        self.moved = asyncio.Event()
        self.gps_ok = False


class _InputUDP(asyncio.DatagramProtocol):
    """Receives RMC (position) and VDM (VHF AIS) from OpenCPN."""

    def __init__(self, state, filt):
        self.st, self.filt = state, filt

    def datagram_received(self, data, addr):
        for line in data.decode(errors="ignore").splitlines():
            line = line.strip()
            if "VDM," in line[:7]:
                if self.filt.feed_vdm(line):
                    print(t("ais_rf_first"))
                continue
            p = parse_rmc(line)
            if p:
                if not self.st.gps_ok:
                    print(t("gps_first_fix", lat=p[0], lon=p[1]))
                    self.st.gps_ok = True
                self.st.lat, self.st.lon = p[0], p[1]
                self.filt.set_gps_time(p[4])
                if distance_nm(self.st.sub_lat, self.st.sub_lon, p[0], p[1]) > self.st.radius / 3:
                    self.st.moved.set()


async def _run(args):
    import websockets  # pip install websockets

    st = _State(args.lat, args.lon, args.radius)
    filt = NetFilter(args.max_age, args.rf_hold * 60, args.tag, args.exclude)
    out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dest = (args.out_host, args.out_port)
    loop = asyncio.get_running_loop()

    if args.gps_port:
        await loop.create_datagram_endpoint(
            lambda: _InputUDP(st, filt), local_addr=("0.0.0.0", args.gps_port))
        print(t("gps_listening", port=args.gps_port))

    if args.test:
        async def test_loop():
            while True:
                for s in test_vessel_sentences(st.lat, st.lon):
                    out.sendto((s + "\r\n").encode(), dest)
                await asyncio.sleep(10)
        asyncio.create_task(test_loop())
        print(t("ais_test"))

    wait = 5
    while True:
        st.sub_lat, st.sub_lon = st.lat, st.lon
        st.moved.clear()
        sub = {"APIKey": args.key, "BoundingBoxes": [bbox(st.sub_lat, st.sub_lon, st.radius)],
               "FilterMessageTypes": MESSAGE_TYPES}
        try:
            async with websockets.connect(AISSTREAM_URL, ping_interval=20, ssl=ssl_context(),
                                          open_timeout=args.timeout) as ws:
                await ws.send(json.dumps(sub))  # must be sent within 3 s
                print(t("ais_connected", radius=st.radius, lat=st.sub_lat, lon=st.sub_lon))
                wait = 5
                t0 = time.time()

                async def reader():
                    nonlocal t0
                    async for raw in ws:
                        msg = json.loads(raw)
                        if "error" in msg:
                            print(t("ais_error", err=msg["error"]))
                            return
                        sentences = filt.process(msg)
                        for s in sentences:
                            out.sendto((s + "\r\n").encode(), dest)
                            if args.verbose:
                                print("   ", s)
                        meta = msg.get("MetaData") or {}
                        if args.diag and sentences and meta.get("latitude") is not None:
                            d = distance_nm(st.lat, st.lon, meta["latitude"], meta["longitude"])
                            print(time.strftime("%H:%M:%S"), t(
                                "ais_diag", type=msg.get("MessageType", ""), mmsi=meta.get("MMSI"),
                                name=(meta.get("ShipName") or "").strip(), dist=d))
                        if time.time() - t0 > 60:
                            s = filt.take_stats()
                            print(time.strftime("%H:%M:%S"), t(
                                "ais_minute", vessels=s["vessels"], sent=s["sent"],
                                old=s["old"], rf=s["rf"]))
                            t0 = time.time()

                rd = asyncio.create_task(reader())
                mv = asyncio.create_task(st.moved.wait())
                done, _ = await asyncio.wait({rd, mv}, return_when=asyncio.FIRST_COMPLETED)
                for task in (rd, mv):
                    task.cancel()
                if mv in done:
                    print(t("ais_moved"))
                    continue
                if not rd.cancelled() and rd.exception():
                    raise rd.exception()
                print(t("ais_closed"))
        except Exception as ex:  # network down, no coverage, etc.
            print(t("conn_lost", err=ex, s=wait))
        await asyncio.sleep(wait)
        wait = min(wait * 2, 120)


def main():
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--lang", default="auto")
    set_language(pre.parse_known_args()[0].lang)

    p = argparse.ArgumentParser(description=t("cli_desc"), parents=[pre])
    p.add_argument("--key", required=True, help="aisstream.io API key")
    p.add_argument("--lat", type=float, required=True, help="initial latitude (decimal degrees)")
    p.add_argument("--lon", type=float, required=True, help="initial longitude (decimal degrees, E positive)")
    p.add_argument("--radius", "--radio", type=float, default=30, help="area radius in NM (default 30)")
    p.add_argument("--out-host", default="127.0.0.1", help="UDP destination (default 127.0.0.1)")
    p.add_argument("--out-port", type=int, default=10111, help="UDP port to OpenCPN (default 10111)")
    p.add_argument("--gps-port", type=int, default=0,
                   help="UDP port where OpenCPN sends RMC/VDM (0 = fixed area)")
    p.add_argument("--exclude", "--excluir", type=int, nargs="*", default=[],
                   help="MMSIs to ignore (own vessel)")
    p.add_argument("--max-age", type=float, default=120,
                   help="drop reports older than N seconds (0 = off, default 120)")
    p.add_argument("--rf-hold", type=float, default=5,
                   help="ignore internet data for N minutes after a vessel is heard on VHF (default 5)")
    p.add_argument("--tag", default=" (NET)", help="suffix for internet vessel names ('' = none)")
    p.add_argument("--timeout", type=float, default=60, help="connection timeout in seconds (default 60)")
    p.add_argument("--diag", action="store_true", help="print every vessel received and its distance")
    p.add_argument("--test", action="store_true", help="send a fake vessel 1 NM away to test OpenCPN")
    p.add_argument("-v", "--verbose", action="store_true", help="print every sentence")
    args = p.parse_args()
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\n" + t("cli_quit"))


if __name__ == "__main__":
    main()
