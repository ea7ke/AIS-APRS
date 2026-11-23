#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EA7KE
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License, version 2 or (at your option)
# any later version. It is distributed WITHOUT ANY WARRANTY. See LICENSE.
"""
AIS + APRS for OpenCPN
======================
A single windowed app that, using the data OpenCPN sends over UDP:
  * AIS:  downloads vessels from aisstream.io and feeds them to OpenCPN,
          giving priority to vessels heard on the local VHF AIS receiver.
  * APRS: publishes the vessel's position to APRS-IS (aprs.fi and similar).

Needs in the same folder:  aisstream_opencpn.py, i18n.py
Library:                   pip install websockets   (AIS only)

OpenCPN (Options -> Connections):
  * AIS input:   Network, UDP, 127.0.0.1, port 10111, "Receive input".
  * Data output: Network, UDP, 127.0.0.1, port 10112, "Output on this port",
                 output filter RMC and VDM.
"""

import asyncio
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

FOLDER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, FOLDER)
import aisstream_opencpn as ais  # noqa: E402
from i18n import t, set_language, LANGUAGES  # noqa: E402

CONFIG = os.path.join(FOLDER, "opencpn_net_config.json")
APRS_VERSION = "OpenCPN-AIS-APRS 1.1.1"

DEFAULTS = {
    # General
    "language": "auto",
    "gps_port": 10112,
    "lat": 0.0,
    "lon": 0.0,
    "open_opencpn": False,
    "opencpn_path": r"C:\Program Files\OpenCPN\opencpn.exe",
    # AIS
    "ais_auto": False,
    "api_key": "",
    "radius": 40,
    "out_host": "127.0.0.1",
    "out_port": 10111,
    "exclude": "",
    "max_age": 120,
    "rf_hold": 5,
    "net_tag": " (NET)",
    "test": False,
    # APRS
    "aprs_auto": False,
    "callsign": "N0CALL-8",
    "passcode": "",
    "server": "euro.aprs2.net",
    "server_port": 14580,
    "symbol": "/Y",
    "comment": "OpenCPN AIS-APRS",
    "int_moving": 5,
    "int_stopped": 30,
    "turn": 30,
}

# Setting names used by version 1.0 (Spanish), mapped to the current ones.
_OLD_KEYS = {
    "abrir_opencpn": "open_opencpn", "ruta_opencpn": "opencpn_path",
    "radio": "radius", "excluir": "exclude", "indicativo": "callsign",
    "servidor": "server", "puerto": "server_port", "simbolo": "symbol",
    "comentario": "comment", "int_movimiento": "int_moving",
    "int_parado": "int_stopped", "giro": "turn",
}


def load_config():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG):
        sources = [CONFIG]
    else:  # first run: reuse the settings of the earlier separate apps
        sources = [os.path.join(FOLDER, "ais_opencpn_config.json"),
                   os.path.join(FOLDER, "aprs_opencpn_config.json")]
    for path in sources:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if path.endswith("ais_opencpn_config.json"):
            data["ais_auto"] = data.pop("autostart", False)
        if path.endswith("aprs_opencpn_config.json"):
            data["aprs_auto"] = data.pop("autostart", False)
            data.pop("gps_port", None)  # the data port is now shared
        for old, new in _OLD_KEYS.items():
            if old in data and new not in data:
                data[new] = data.pop(old)
        cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
    return cfg


def save_config(cfg):
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


# --------------------------------------------------------------------------
# APRS helpers
# --------------------------------------------------------------------------

def passcode(callsign):
    """Standard APRS-IS passcode for a callsign (SSID ignored)."""
    c = callsign.upper().split("-")[0]
    h = 0x73E2
    for i in range(0, len(c), 2):
        h ^= ord(c[i]) << 8
        if i + 1 < len(c):
            h ^= ord(c[i + 1])
    return h & 0x7FFF


def aprs_frame(cfg, lat, lon, sog, cog):
    """Uncompressed APRS position packet, with course/speed when moving."""
    table, sym = cfg["symbol"][0], cfg["symbol"][1]

    def dm(value):  # degrees and minutes (hundredths), never "60.00"
        cent = round(abs(value) * 6000)
        return cent // 6000, (cent % 6000) / 100

    d, m = dm(lat)
    lat_s = f"{d:02d}{m:05.2f}{'N' if lat >= 0 else 'S'}"
    d, m = dm(lon)
    lon_s = f"{d:03d}{m:05.2f}{'E' if lon >= 0 else 'W'}"
    ext = ""
    if sog is not None and sog >= 0.5:
        cse = int(round(cog or 0)) % 360 or 360
        ext = f"{cse:03d}/{min(int(round(sog)), 999):03d}"
    body = f"!{lat_s}{table}{lon_s}{sym}{ext}{cfg['comment'][:43]}"
    return f"{cfg['callsign'].upper()}>APRS,TCPIP*:{body}"


# --------------------------------------------------------------------------
# Background engine: shared OpenCPN input + AIS and APRS services
# --------------------------------------------------------------------------

class Position:
    def __init__(self):
        self.lat = self.lon = self.sog = self.cog = None
        self.t = 0.0

    def valid(self, max_age=120):
        return self.lat is not None and time.time() - self.t <= max_age


class Engine:
    def __init__(self, q):
        self.q = q                       # messages to the window
        self.pos = Position()
        self.filter = None               # NetFilter of the running AIS service
        self.loop = asyncio.new_event_loop()
        self.udp = None
        self.ais_fut = None
        self.aprs_fut = None
        self.beacon_now = False
        threading.Thread(target=self._thread, daemon=True).start()

    def _thread(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    # ---- notifications to the window (thread-safe through the queue)
    def log(self, source, text):
        self.q.put(("log", f"{time.strftime('%H:%M:%S')}  [{source}] {text}"))

    def status(self, **kw):
        self.q.put(("status", kw))

    def _launch(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    # ---- shared input from OpenCPN (RMC position + VDM from the VHF)
    def open_input(self, port):
        self._launch(self._open_input(int(port)))

    async def _open_input(self, port):
        if self.udp:
            self.udp.close()
            self.udp = None
        if not port:
            self.status(gps=t("st_gps_none"))
            return
        engine = self

        class Input(asyncio.DatagramProtocol):
            def datagram_received(self, data, addr):
                for line in data.decode(errors="ignore").splitlines():
                    engine._on_line(line.strip())

        try:
            self.udp, _ = await self.loop.create_datagram_endpoint(
                Input, local_addr=("0.0.0.0", port))
            self.log("GPS", t("gps_listening", port=port))
            self.status(gps=t("st_gps_waiting", port=port))
        except OSError as ex:
            self.log("GPS", t("gps_port_error", port=port, err=ex))
            self.status(gps=t("st_gps_error", port=port))

    def _on_line(self, line):
        if "VDM," in line[:7]:
            if self.filter and self.filter.feed_vdm(line):
                self.log("AIS", t("ais_rf_first"))
            return
        p = ais.parse_rmc(line)
        if not p:
            return
        if self.pos.lat is None:
            self.log("GPS", t("gps_first_fix", lat=p[0], lon=p[1]))
        self.pos.lat, self.pos.lon, self.pos.sog, self.pos.cog = p[:4]
        self.pos.t = time.time()
        if self.filter:
            self.filter.set_gps_time(p[4])
        self.status(gps=t("st_gps_value", lat=p[0], lon=p[1], sog=p[2],
                          time=time.strftime("%H:%M:%S")))

    # ---- service control (called from the window)
    def start_ais(self, cfg):
        if not self.ais_running():
            self.ais_fut = self._launch(self._ais(cfg))

    def stop_ais(self):
        if self.ais_fut:
            self.loop.call_soon_threadsafe(self.ais_fut.cancel)

    def start_aprs(self, cfg):
        if not self.aprs_running():
            self.beacon_now = False
            self.aprs_fut = self._launch(self._aprs(cfg))

    def stop_aprs(self):
        if self.aprs_fut:
            self.loop.call_soon_threadsafe(self.aprs_fut.cancel)

    def request_beacon(self):
        self.beacon_now = True

    def ais_running(self):
        return bool(self.ais_fut and not self.ais_fut.done())

    def aprs_running(self):
        return bool(self.aprs_fut and not self.aprs_fut.done())

    # ---- AIS service
    async def _ais(self, c):
        try:
            import websockets
        except ImportError:
            self.log("AIS", t("ais_no_websockets"))
            self.status(ais=t("ais_no_websockets"))
            self.q.put(("end", "ais"))
            return
        out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        dest = (c["out_host"], int(c["out_port"]))
        exclude = {int(x) for x in str(c["exclude"]).replace(",", " ").split() if x.isdigit()}
        self.filter = ais.NetFilter(float(c["max_age"]), float(c["rf_hold"]) * 60,
                                    c["net_tag"], exclude)
        radius = float(c["radius"])
        lat, lon = float(c["lat"]), float(c["lon"])
        test = None
        if c["test"]:
            test = asyncio.create_task(self._ais_test(out, dest, lat, lon))
            self.log("AIS", t("ais_test"))
        wait = 5
        try:
            while True:
                if self.pos.valid():
                    lat, lon = self.pos.lat, self.pos.lon
                centre = (lat, lon)
                sub = {"APIKey": c["api_key"].strip(),
                       "BoundingBoxes": [ais.bbox(lat, lon, radius)],
                       "FilterMessageTypes": ais.MESSAGE_TYPES}
                self.status(ais=t("connecting"))
                try:
                    async with websockets.connect(ais.AISSTREAM_URL, ping_interval=20,
                                                  ssl=ais.ssl_context(),
                                                  open_timeout=60) as ws:
                        await ws.send(json.dumps(sub))
                        self.status(ais=t("connected"))
                        self.log("AIS", t("ais_connected", radius=radius,
                                          lat=centre[0], lon=centre[1]))
                        wait = 5
                        reader = asyncio.create_task(self._ais_read(ws, out, dest))
                        try:
                            while not reader.done():
                                await asyncio.wait({reader}, timeout=5)
                                if self.pos.valid() and ais.distance_nm(
                                        *centre, self.pos.lat, self.pos.lon) > radius / 3:
                                    self.log("AIS", t("ais_moved"))
                                    break
                            if reader.done():
                                if reader.exception():
                                    raise reader.exception()
                                self.log("AIS", t("ais_closed"))
                            else:
                                continue
                        finally:
                            reader.cancel()
                except asyncio.CancelledError:
                    raise
                except Exception as ex:
                    self.log("AIS", t("conn_lost", err=ex, s=wait))
                self.status(ais=t("reconnecting"))
                await asyncio.sleep(wait)
                wait = min(wait * 2, 120)
        except asyncio.CancelledError:
            pass
        finally:
            if test:
                test.cancel()
            out.close()
            self.filter = None
            self.log("AIS", t("stopped"))
            self.status(ais=t("stopped"), traffic="—")
            self.q.put(("end", "ais"))

    async def _ais_test(self, out, dest, lat, lon):
        while True:
            if self.pos.valid():
                lat, lon = self.pos.lat, self.pos.lon
            for s in ais.test_vessel_sentences(lat, lon):
                out.sendto((s + "\r\n").encode(), dest)
            await asyncio.sleep(10)

    async def _ais_read(self, ws, out, dest):
        t0 = time.time()
        async for raw in ws:
            msg = json.loads(raw)
            if "error" in msg:
                self.log("AIS", t("ais_error", err=msg["error"]))
                return
            for s in self.filter.process(msg):
                out.sendto((s + "\r\n").encode(), dest)
            if time.time() - t0 > 60:
                st = self.filter.take_stats()
                self.status(traffic=t("ais_minute", vessels=st["vessels"], sent=st["sent"],
                                      old=st["old"], rf=st["rf"]))
                t0 = time.time()

    # ---- APRS service
    async def _aprs(self, c):
        wait = 5
        try:
            while True:
                try:
                    await self._aprs_session(c)
                    wait = 5
                except asyncio.CancelledError:
                    raise
                except Exception as ex:
                    self.log("APRS", t("conn_lost", err=ex, s=wait))
                self.status(aprs=t("reconnecting"))
                await asyncio.sleep(wait)
                wait = min(wait * 2, 120)
        except asyncio.CancelledError:
            pass
        finally:
            self.log("APRS", t("stopped"))
            self.status(aprs=t("stopped"))
            self.q.put(("end", "aprs"))

    async def _aprs_session(self, c):
        self.status(aprs=t("connecting"))
        r, w = await asyncio.wait_for(
            asyncio.open_connection(c["server"], int(c["server_port"])), 30)
        last = None  # (time, course) of the last beacon
        try:
            w.write(f"user {c['callsign'].upper()} pass {c['passcode']} "
                    f"vers {APRS_VERSION}\r\n".encode())
            await w.drain()

            async def reader():
                while True:
                    line = await asyncio.wait_for(r.readline(), 120)
                    if not line:
                        raise ConnectionError(t("aprs_server_closed"))
                    text = line.decode(errors="ignore").strip()
                    if text.startswith("# logresp"):
                        ok = " verified" in text
                        self.log("APRS", text[2:])
                        self.status(aprs=t("aprs_connected_to", server=c["server"])
                                    + ("" if ok else t("aprs_unverified")))

            rd = asyncio.create_task(reader())
            try:
                while True:
                    await asyncio.wait({rd}, timeout=5)
                    if rd.done():
                        rd.result()  # re-raise the error
                    if self._beacon_due(c, last):
                        p = self.pos
                        frame = aprs_frame(c, p.lat, p.lon, p.sog, p.cog)
                        w.write((frame + "\r\n").encode())
                        await w.drain()
                        last = (time.time(), p.cog)
                        self.beacon_now = False
                        self.log("APRS", t("aprs_sent", frame=frame))
                        self.status(beacon=time.strftime("%H:%M:%S"))
            finally:
                rd.cancel()
        finally:
            w.close()

    def _beacon_due(self, c, last):
        p = self.pos
        if not p.valid():
            return False                 # no recent GPS: never send an old position
        if self.beacon_now or last is None:
            return True
        elapsed = time.time() - last[0]
        moving = (p.sog or 0) >= 1.0
        if moving and elapsed >= float(c["int_moving"]) * 60:
            return True
        if not moving and elapsed >= float(c["int_stopped"]) * 60:
            return True
        if moving and elapsed >= 60 and p.cog is not None and last[1] is not None:
            turn = abs((p.cog - last[1] + 180) % 360 - 180)
            if turn >= float(c["turn"]):
                return True
        return False


# --------------------------------------------------------------------------
# Window
# --------------------------------------------------------------------------

STATUS_ROWS = [("gps", "st_gps"), ("ais", "st_ais"), ("traffic", "st_traffic"),
               ("aprs", "st_aprs"), ("beacon", "st_beacon")]


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.resizable(False, False)
        self.cfg = load_config()
        set_language(self.cfg["language"])
        self.q = queue.Queue()
        self.engine = Engine(self.q)
        self.log_lines = []
        self.s = {k: tk.StringVar(value="—") for k, _ in STATUS_ROWS}
        self.s["ais"].set(t("stopped"))
        self.s["aprs"].set(t("stopped"))
        self.build()

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(300, self.poll_queue)
        self.engine.open_input(self.cfg["gps_port"])
        if self.cfg.get("open_opencpn"):
            self.open_opencpn()
        if self.cfg.get("ais_auto") and self.cfg.get("api_key"):
            self.after(500, self.toggle_ais)
        if self.cfg.get("aprs_auto") and self.cfg.get("passcode"):
            self.after(700, self.toggle_aprs)

    # ---- building the interface (rebuilt when the language changes)
    def build(self):
        for child in self.winfo_children():
            child.destroy()
        self.title(t("app_title"))
        self.v = {}

        nb = ttk.Notebook(self)
        nb.grid(padx=8, pady=8)
        tabs = {}
        for key in ("tab_status", "tab_ais", "tab_aprs", "tab_general"):
            tabs[key] = ttk.Frame(nb, padding=10)
            nb.add(tabs[key], text=t(key))

        # Status tab
        tab = tabs["tab_status"]
        grid = ttk.Frame(tab)
        grid.grid(row=0, column=0, sticky="w")
        for i, (k, label) in enumerate(STATUS_ROWS):
            ttk.Label(grid, text=t(label) + ":").grid(row=i, column=0, sticky="w", padx=(0, 8))
            ttk.Label(grid, textvariable=self.s[k]).grid(row=i, column=1, sticky="w")
        bar = ttk.Frame(tab, padding=(0, 8))
        bar.grid(row=1, column=0, sticky="w")
        self.b_ais = ttk.Button(bar, width=16, command=self.toggle_ais)
        self.b_ais.pack(side="left")
        self.b_aprs = ttk.Button(bar, width=16, command=self.toggle_aprs)
        self.b_aprs.pack(side="left", padx=4)
        self.b_now = ttk.Button(bar, text=t("btn_beacon_now"), command=self.beacon_now)
        self.b_now.pack(side="left")
        ttk.Button(bar, text=t("btn_open_opencpn"), command=self.open_opencpn).pack(side="left", padx=4)
        ttk.Button(bar, text=t("btn_minimize"), command=self.iconify).pack(side="left")
        self.txt = tk.Text(tab, width=96, height=14, state="disabled", font=("Consolas", 9))
        self.txt.grid(row=2, column=0)
        self._refill_log()
        self._refresh_buttons()

        # AIS tab
        row = self._fields(tabs["tab_ais"], [
            ("f_api_key", "api_key", 44, "secret"),
            ("f_radius", "radius", 8, None),
            ("f_out_host", "out_host", 16, None),
            ("f_out_port", "out_port", 8, None),
            ("f_exclude", "exclude", 24, None),
            ("f_max_age", "max_age", 8, None),
            ("f_rf_hold", "rf_hold", 8, None),
            ("f_net_tag", "net_tag", 10, None),
        ], [("test", "c_test"), ("ais_auto", "c_ais_auto")])
        ttk.Label(tabs["tab_ais"], text=t("ais_hint"), foreground="gray").grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(8, 0))

        # APRS tab
        row = self._fields(tabs["tab_aprs"], [
            ("f_callsign", "callsign", 14, "passcode"),
            ("f_passcode", "passcode", 10, None),
            ("f_server", "server", 24, None),
            ("f_server_port", "server_port", 8, None),
            ("f_symbol", "symbol", 6, None),
            ("f_comment", "comment", 40, None),
            ("f_int_moving", "int_moving", 6, None),
            ("f_int_stopped", "int_stopped", 6, None),
            ("f_turn", "turn", 6, None),
        ], [("aprs_auto", "c_aprs_auto")])
        ttk.Label(tabs["tab_aprs"], text=t("aprs_hint"), foreground="gray").grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(8, 0))

        # General tab
        gen = tabs["tab_general"]
        ttk.Label(gen, text=t("f_language")).grid(row=0, column=0, sticky="w", pady=1)
        self.lang_names = {"auto": t("lang_auto"), **LANGUAGES}
        self.lang_var = tk.StringVar(value=self.lang_names.get(self.cfg["language"], t("lang_auto")))
        combo = ttk.Combobox(gen, textvariable=self.lang_var, state="readonly", width=14,
                             values=list(self.lang_names.values()))
        combo.grid(row=0, column=1, sticky="w", pady=1)
        combo.bind("<<ComboboxSelected>>", self.on_language)
        row = self._fields(gen, [
            ("f_gps_port", "gps_port", 8, None),
            ("f_lat", "lat", 12, None),
            ("f_lon", "lon", 12, None),
            ("f_opencpn_path", "opencpn_path", 44, "browse"),
        ], [("open_opencpn", "c_open_opencpn")], start=1)
        ttk.Label(gen, text=t("general_hint"), foreground="gray").grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(8, 0))

        bottom = ttk.Frame(self, padding=(8, 0, 8, 8))
        bottom.grid(row=1, column=0, sticky="ew")
        ttk.Button(bottom, text=t("btn_save"), command=self.save).pack(side="left")
        ttk.Label(bottom, text=t("save_hint"), foreground="gray").pack(side="left", padx=6)

    def _fields(self, parent, fields, checks, start=0):
        row = start
        for label, key, width, extra in fields:
            ttk.Label(parent, text=t(label)).grid(row=row, column=0, sticky="w", pady=1)
            var = tk.StringVar(value=str(self.cfg[key]))
            entry = ttk.Entry(parent, textvariable=var, width=width,
                              show="•" if extra == "secret" else "")
            entry.grid(row=row, column=1, sticky="w", pady=1)
            self.v[key] = var
            if extra == "secret":
                show = tk.BooleanVar(value=False)
                ttk.Checkbutton(parent, text=t("show"), variable=show,
                                command=lambda e=entry, b=show: e.config(show="" if b.get() else "•")
                                ).grid(row=row, column=2, sticky="w")
            elif extra == "passcode":
                ttk.Button(parent, text=t("btn_calc_passcode"), command=self.calc_passcode
                           ).grid(row=row, column=2, sticky="w", padx=4)
            elif extra == "browse":
                ttk.Button(parent, text="…", width=3, command=self.browse_opencpn
                           ).grid(row=row, column=2, sticky="w")
            row += 1
        for key, label in checks:
            var = tk.BooleanVar(value=bool(self.cfg[key]))
            ttk.Checkbutton(parent, text=t(label), variable=var).grid(
                row=row, column=0, columnspan=3, sticky="w")
            self.v[key] = var
            row += 1
        return row

    def on_language(self, _event=None):
        name = self.lang_var.get()
        code = next((k for k, v in self.lang_names.items() if v == name), "auto")
        # keep what has been typed but not saved yet
        self.cfg.update({k: v.get() for k, v in self.v.items()})
        self.cfg["language"] = code
        set_language(code)
        try:
            save_config(self.read() or self.cfg)
        except OSError:
            pass
        self.build()

    # ---- settings
    def read(self):
        cfg = {k: v.get() for k, v in self.v.items()}
        cfg["language"] = self.cfg.get("language", "auto")
        cfg["callsign"] = cfg["callsign"].strip().upper()
        cfg["symbol"] = (cfg["symbol"].strip() + "  ")[:2]
        try:
            for k in ("lat", "lon", "radius", "int_moving", "int_stopped", "turn",
                      "max_age", "rf_hold"):
                cfg[k] = float(str(cfg[k]).replace(",", "."))
            for k in ("gps_port", "out_port", "server_port"):
                cfg[k] = int(cfg[k])
        except ValueError:
            messagebox.showerror(t("err_title"), t("err_numbers"))
            return None
        if cfg["int_moving"] < 1 or cfg["int_stopped"] < 5:
            messagebox.showerror(t("err_title"), t("err_aprs_min"))
            return None
        return cfg

    def save(self):
        cfg = self.read()
        if not cfg:
            return None
        if cfg["gps_port"] != self.cfg.get("gps_port"):
            self.engine.open_input(cfg["gps_port"])
        self.cfg = cfg
        save_config(cfg)
        self.log(t("settings_saved"))
        return cfg

    def calc_passcode(self):
        call = self.v["callsign"].get().strip()
        if call:
            self.v["passcode"].set(str(passcode(call)))

    def browse_opencpn(self):
        path = filedialog.askopenfilename(title=t("select_opencpn"),
                                          filetypes=[(t("program"), "*.exe"), (t("all_files"), "*.*")])
        if path:
            self.v["opencpn_path"].set(path)

    def open_opencpn(self):
        path = self.v["opencpn_path"].get() if hasattr(self, "v") else self.cfg["opencpn_path"]
        try:
            subprocess.Popen([path], cwd=os.path.dirname(path) or None)
            self.log(t("opening_opencpn"))
        except OSError as ex:
            self.log(t("opencpn_error", err=ex))

    # ---- services
    def _refresh_buttons(self):
        self.b_ais.config(text=t("btn_stop_ais") if self.engine.ais_running() else t("btn_start_ais"),
                          state="normal")
        self.b_aprs.config(text=t("btn_stop_aprs") if self.engine.aprs_running() else t("btn_start_aprs"),
                           state="normal")
        self.b_now.config(state="normal" if self.engine.aprs_running() else "disabled")

    def toggle_ais(self):
        if self.engine.ais_running():
            self.engine.stop_ais()
            self.b_ais.config(state="disabled")
            return
        cfg = self.save()
        if not cfg:
            return
        if not cfg["api_key"].strip():
            messagebox.showwarning(t("missing_key_title"), t("missing_key"))
            return
        self.engine.start_ais(cfg)
        self.b_ais.config(text=t("btn_stop_ais"))

    def toggle_aprs(self):
        if self.engine.aprs_running():
            self.engine.stop_aprs()
            self.b_aprs.config(state="disabled")
            return
        cfg = self.save()
        if not cfg:
            return
        if not str(cfg["passcode"]).strip():
            messagebox.showwarning(t("missing_pass_title"), t("missing_pass"))
            return
        self.engine.start_aprs(cfg)
        self.b_aprs.config(text=t("btn_stop_aprs"))
        self.b_now.config(state="normal")

    def beacon_now(self):
        self.engine.request_beacon()
        self.log(t("aprs_beacon_requested"))

    # ---- log and queue
    def log(self, text):
        if not text[:2].isdigit():
            text = f"{time.strftime('%H:%M:%S')}  {text}"
        self.log_lines = (self.log_lines + [text])[-400:]
        self.txt.config(state="normal")
        self.txt.insert("end", text + "\n")
        n = int(self.txt.index("end-1c").split(".")[0])
        if n > 400:
            self.txt.delete("1.0", f"{n - 400}.0")
        self.txt.see("end")
        self.txt.config(state="disabled")

    def _refill_log(self):
        self.txt.config(state="normal")
        self.txt.insert("end", "".join(line + "\n" for line in self.log_lines))
        self.txt.see("end")
        self.txt.config(state="disabled")

    def poll_queue(self):
        try:
            while True:
                kind, data = self.q.get_nowait()
                if kind == "log":
                    self.log(data)
                elif kind == "status":
                    for k, val in data.items():
                        self.s[k].set(val)
                elif kind == "end":
                    self._refresh_buttons()
        except queue.Empty:
            pass
        self.after(300, self.poll_queue)

    def on_close(self):
        if (self.engine.ais_running() or self.engine.aprs_running()) and not messagebox.askyesno(
                t("quit_title"), t("quit_question")):
            return
        self.engine.stop_ais()
        self.engine.stop_aprs()
        self.after(500, self.destroy)


if __name__ == "__main__":
    App().mainloop()
