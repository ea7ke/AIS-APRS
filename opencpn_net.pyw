#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EA7KE
# Este programa es software libre: puede redistribuirlo y/o modificarlo bajo
# los términos de la Licencia Pública General GNU, versión 2 o (a su elección)
# cualquier versión posterior. Se distribuye SIN NINGUNA GARANTÍA. Ver LICENSE.
"""
AIS + APRS para OpenCPN
=======================
Una sola app con ventana que, usando la posición GPS que envía OpenCPN:
  * AIS:  descarga barcos de aisstream.io y los envía a OpenCPN por UDP.
  * APRS: publica la posición del barco en APRS-IS (aprs.fi y similares).

Necesita en la misma carpeta:  aisstream_opencpn.py
Librería:                      pip install websockets   (solo para el AIS)

OpenCPN (Opciones -> Conexiones):
  * Entrada AIS:  Red, UDP, 127.0.0.1, puerto 10111, "Recibir datos".
  * Salida GPS:   Red, UDP, 127.0.0.1, puerto 10112, "Enviar datos", filtro RMC.
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

CARPETA = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CARPETA)
import aisstream_opencpn as ais  # noqa: E402

CONFIG = os.path.join(CARPETA, "opencpn_net_config.json")
VERSION_APRS = "OpenCPN-AIS-APRS 1.0"

POR_DEFECTO = {
    # General
    "gps_port": 10112,
    "lat": 0.0,
    "lon": 0.0,
    "abrir_opencpn": False,
    "ruta_opencpn": r"C:\Program Files\OpenCPN\opencpn.exe",
    # AIS
    "ais_auto": False,
    "api_key": "",
    "radio": 40,
    "out_host": "127.0.0.1",
    "out_port": 10111,
    "excluir": "",
    "test": False,
    # APRS
    "aprs_auto": False,
    "indicativo": "N0CALL-8",
    "passcode": "",
    "servidor": "euro.aprs2.net",
    "puerto": 14580,
    "simbolo": "/Y",
    "comentario": "OpenCPN AIS-APRS",
    "int_movimiento": 5,
    "int_parado": 30,
    "giro": 30,
}


def cargar_config():
    cfg = dict(POR_DEFECTO)
    if os.path.exists(CONFIG):
        fuentes = [CONFIG]
    else:  # primera vez: aprovechar la configuración de las apps anteriores
        fuentes = [os.path.join(CARPETA, "ais_opencpn_config.json"),
                   os.path.join(CARPETA, "aprs_opencpn_config.json")]
    for ruta in fuentes:
        try:
            with open(ruta, encoding="utf-8") as f:
                datos = json.load(f)
        except (OSError, ValueError):
            continue
        if ruta.endswith("ais_opencpn_config.json"):
            datos["ais_auto"] = datos.pop("autostart", False)
        if ruta.endswith("aprs_opencpn_config.json"):
            datos["aprs_auto"] = datos.pop("autostart", False)
            datos.pop("gps_port", None)  # ahora el GPS es común
        cfg.update({k: v for k, v in datos.items() if k in POR_DEFECTO})
    return cfg


def guardar_config(cfg):
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


# --------------------------------------------------------------------------
# APRS: utilidades
# --------------------------------------------------------------------------

def passcode(indicativo):
    c = indicativo.upper().split("-")[0]
    h = 0x73E2
    for i in range(0, len(c), 2):
        h ^= ord(c[i]) << 8
        if i + 1 < len(c):
            h ^= ord(c[i + 1])
    return h & 0x7FFF


def parse_rmc(linea):
    """(lat, lon, velocidad_kn, rumbo) o None."""
    try:
        if not linea.startswith("$"):
            return None
        c = linea.split("*")[0].split(",")
        if not c[0].endswith("RMC") or c[2] != "A":
            return None
        lat = int(c[3][:2]) + float(c[3][2:]) / 60
        if c[4] == "S":
            lat = -lat
        lon = int(c[5][:3]) + float(c[5][3:]) / 60
        if c[6] == "W":
            lon = -lon
        vel = float(c[7]) if c[7] else 0.0
        rumbo = float(c[8]) if c[8] else None
        return lat, lon, vel, rumbo
    except (IndexError, ValueError):
        return None


def trama_aprs(cfg, lat, lon, vel, rumbo):
    tabla, sim = cfg["simbolo"][0], cfg["simbolo"][1]

    def gm(valor):
        cent = round(abs(valor) * 6000)
        return cent // 6000, (cent % 6000) / 100

    g, mi = gm(lat)
    lat_s = f"{g:02d}{mi:05.2f}{'N' if lat >= 0 else 'S'}"
    g, mi = gm(lon)
    lon_s = f"{g:03d}{mi:05.2f}{'E' if lon >= 0 else 'W'}"
    ext = ""
    if vel is not None and vel >= 0.5:
        cse = int(round(rumbo or 0)) % 360 or 360
        ext = f"{cse:03d}/{min(int(round(vel)), 999):03d}"
    cuerpo = f"!{lat_s}{tabla}{lon_s}{sim}{ext}{cfg['comentario'][:43]}"
    return f"{cfg['indicativo'].upper()}>APRS,TCPIP*:{cuerpo}"


# --------------------------------------------------------------------------
# Motor en segundo plano: GPS común + servicios AIS y APRS
# --------------------------------------------------------------------------

class Posicion:
    def __init__(self):
        self.lat = self.lon = self.vel = self.rumbo = None
        self.t = 0.0

    def valida(self, max_edad=120):
        return self.lat is not None and time.time() - self.t <= max_edad


class Motor:
    def __init__(self, cola):
        self.cola = cola
        self.pos = Posicion()
        self.loop = asyncio.new_event_loop()
        self.gps_transp = None
        self.ais_fut = None
        self.aprs_fut = None
        self.aprs_forzar = False
        threading.Thread(target=self._hilo, daemon=True).start()

    def _hilo(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    # --- avisos a la ventana ---
    def log(self, origen, t):
        self.cola.put(("log", f"{time.strftime('%H:%M:%S')}  [{origen}] {t}"))

    def estado(self, **kw):
        self.cola.put(("estado", kw))

    def _lanzar(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    # --- GPS común ---
    def abrir_gps(self, puerto):
        self._lanzar(self._abrir_gps(int(puerto)))

    async def _abrir_gps(self, puerto):
        if self.gps_transp:
            self.gps_transp.close()
            self.gps_transp = None
        if not puerto:
            self.estado(gps="sin GPS (puerto 0)")
            return
        motor = self

        class Gps(asyncio.DatagramProtocol):
            def datagram_received(self, data, addr):
                for linea in data.decode(errors="ignore").splitlines():
                    p = parse_rmc(linea.strip())
                    if p:
                        if motor.pos.lat is None:
                            motor.log("GPS", f"Recibido de OpenCPN: {p[0]:.4f}, {p[1]:.4f}")
                        pos = motor.pos
                        pos.lat, pos.lon, pos.vel, pos.rumbo = p
                        pos.t = time.time()
                        motor.estado(gps=f"{p[0]:.4f}, {p[1]:.4f}   {p[2]:.1f} kn   "
                                         f"({time.strftime('%H:%M:%S')})")
        try:
            self.gps_transp, _ = await self.loop.create_datagram_endpoint(
                Gps, local_addr=("0.0.0.0", puerto))
            self.log("GPS", f"Escuchando posición de OpenCPN en UDP {puerto}")
            self.estado(gps=f"esperando datos en UDP {puerto}…")
        except OSError as ex:
            self.log("GPS", f"No puedo abrir el puerto {puerto}: {ex}")
            self.estado(gps=f"ERROR puerto {puerto}")

    # --- control de servicios (llamar desde la ventana) ---
    def iniciar_ais(self, cfg):
        if not self.ais_fut or self.ais_fut.done():
            self.ais_fut = self._lanzar(self._ais(cfg))

    def parar_ais(self):
        if self.ais_fut:
            self.loop.call_soon_threadsafe(self.ais_fut.cancel)

    def iniciar_aprs(self, cfg):
        if not self.aprs_fut or self.aprs_fut.done():
            self.aprs_forzar = False
            self.aprs_fut = self._lanzar(self._aprs(cfg))

    def parar_aprs(self):
        if self.aprs_fut:
            self.loop.call_soon_threadsafe(self.aprs_fut.cancel)

    def aprs_ya(self):
        self.aprs_forzar = True

    def ais_activo(self):
        return bool(self.ais_fut and not self.ais_fut.done())

    def aprs_activo(self):
        return bool(self.aprs_fut and not self.aprs_fut.done())

    # --- servicio AIS ---
    async def _ais(self, c):
        try:
            import websockets
        except ImportError:
            self.log("AIS", "Falta la librería websockets (pip install websockets)")
            self.estado(ais="Error: falta websockets")
            self.cola.put(("fin", "ais"))
            return
        salida = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        destino = (c["out_host"], int(c["out_port"]))
        excluir = {int(x) for x in str(c["excluir"]).replace(",", " ").split() if x.isdigit()}
        radio = float(c["radio"])
        est = ais.Estado(float(c["lat"]), float(c["lon"]), radio)
        prueba = None
        if c["test"]:
            prueba = asyncio.create_task(self._ais_prueba(est, salida, destino))
            self.log("AIS", "Enviando barco de prueba 'PRUEBA SCRIPT' 1 milla al norte")
        espera = 5
        try:
            while True:
                if self.pos.valida():
                    est.lat, est.lon = self.pos.lat, self.pos.lon
                centro = (est.lat, est.lon)
                sub = {"APIKey": c["api_key"].strip(),
                       "BoundingBoxes": [ais.caja(*centro, radio)],
                       "FilterMessageTypes": ais.TIPOS}
                self.estado(ais="Conectando…")
                try:
                    async with websockets.connect(ais.AISSTREAM_URL, ping_interval=20,
                                                  open_timeout=60) as ws:
                        await ws.send(json.dumps(sub))
                        self.estado(ais="Conectado")
                        self.log("AIS", f"Conectado. Zona {radio:.0f} MN alrededor de "
                                        f"{centro[0]:.4f}, {centro[1]:.4f}")
                        espera = 5
                        lector = asyncio.create_task(self._ais_leer(ws, salida, destino, excluir))
                        try:
                            while not lector.done():
                                await asyncio.wait({lector}, timeout=5)
                                if self.pos.valida() and ais.distancia_mn(
                                        *centro, self.pos.lat, self.pos.lon) > radio / 3:
                                    self.log("AIS", "El barco se ha movido: recentrando zona")
                                    break
                            if lector.done():
                                if lector.exception():
                                    raise lector.exception()
                                self.log("AIS", "aisstream ha cerrado la conexión")
                            else:
                                continue
                        finally:
                            lector.cancel()
                except asyncio.CancelledError:
                    raise
                except Exception as ex:
                    self.log("AIS", f"Conexión perdida ({ex}). Reintento en {espera} s")
                self.estado(ais="Reconectando…")
                await asyncio.sleep(espera)
                espera = min(espera * 2, 120)
        except asyncio.CancelledError:
            pass
        finally:
            if prueba:
                prueba.cancel()
            salida.close()
            self.log("AIS", "Parado")
            self.estado(ais="Parado", trafico="—")
            self.cola.put(("fin", "ais"))

    async def _ais_prueba(self, est, salida, destino):
        while True:
            if self.pos.valida():
                est.lat, est.lon = self.pos.lat, self.pos.lon
            m = {"Latitude": est.lat + 1 / 60, "Longitude": est.lon, "Sog": 0, "Cog": 0,
                 "TrueHeading": 0, "NavigationalStatus": 1, "Timestamp": int(time.time()) % 60}
            frases = ais.nmea(ais.enc_tipo1(999000001, m))
            frases += ais.nmea(ais.enc_tipo5(999000001, {"Name": "PRUEBA SCRIPT", "Type": 37}))
            for f in frases:
                salida.sendto((f + "\r\n").encode(), destino)
            await asyncio.sleep(10)

    async def _ais_leer(self, ws, salida, destino, excluir):
        n, barcos, t0 = 0, set(), time.time()
        async for raw in ws:
            msg = json.loads(raw)
            if "error" in msg:
                self.log("AIS", f"Error de aisstream: {msg['error']}")
                return
            mmsi = (msg.get("MetaData") or {}).get("MMSI")
            if mmsi in excluir:
                continue
            for frase in ais.convertir(msg):
                salida.sendto((frase + "\r\n").encode(), destino)
                n += 1
            barcos.add(mmsi)
            if time.time() - t0 > 60:
                self.estado(trafico=f"{len(barcos)} barcos, {n} sentencias (último minuto)")
                n, barcos, t0 = 0, set(), time.time()

    # --- servicio APRS ---
    async def _aprs(self, c):
        espera = 5
        try:
            while True:
                try:
                    await self._aprs_sesion(c)
                    espera = 5
                except asyncio.CancelledError:
                    raise
                except Exception as ex:
                    self.log("APRS", f"Conexión perdida ({ex}). Reintento en {espera} s")
                self.estado(aprs="Reconectando…")
                await asyncio.sleep(espera)
                espera = min(espera * 2, 120)
        except asyncio.CancelledError:
            pass
        finally:
            self.log("APRS", "Parado")
            self.estado(aprs="Parado")
            self.cola.put(("fin", "aprs"))

    async def _aprs_sesion(self, c):
        self.estado(aprs="Conectando…")
        r, w = await asyncio.wait_for(
            asyncio.open_connection(c["servidor"], int(c["puerto"])), 30)
        ultima = None  # (hora, rumbo)
        try:
            w.write(f"user {c['indicativo'].upper()} pass {c['passcode']} "
                    f"vers {VERSION_APRS}\r\n".encode())
            await w.drain()

            async def leer():
                while True:
                    linea = await asyncio.wait_for(r.readline(), 120)
                    if not linea:
                        raise ConnectionError("el servidor cerró la conexión")
                    t = linea.decode(errors="ignore").strip()
                    if t.startswith("# logresp"):
                        ok = " verified" in t
                        self.log("APRS", t[2:])
                        self.estado(aprs=f"Conectado a {c['servidor']}"
                                    + ("" if ok else " (SIN VERIFICAR: revisa el passcode)"))

            lector = asyncio.create_task(leer())
            try:
                while True:
                    await asyncio.wait({lector}, timeout=5)
                    if lector.done():
                        lector.result()
                    if self._aprs_toca(c, ultima):
                        p = self.pos
                        trama = trama_aprs(c, p.lat, p.lon, p.vel, p.rumbo)
                        w.write((trama + "\r\n").encode())
                        await w.drain()
                        ultima = (time.time(), p.rumbo)
                        self.aprs_forzar = False
                        self.log("APRS", f"Enviado: {trama}")
                        self.estado(baliza=time.strftime("%H:%M:%S"))
            finally:
                lector.cancel()
        finally:
            w.close()

    def _aprs_toca(self, c, ultima):
        p = self.pos
        if not p.valida():
            return False
        if self.aprs_forzar or ultima is None:
            return True
        dt = time.time() - ultima[0]
        navegando = (p.vel or 0) >= 1.0
        if navegando and dt >= float(c["int_movimiento"]) * 60:
            return True
        if not navegando and dt >= float(c["int_parado"]) * 60:
            return True
        if navegando and dt >= 60 and p.rumbo is not None and ultima[1] is not None:
            giro = abs((p.rumbo - ultima[1] + 180) % 360 - 180)
            if giro >= float(c["giro"]):
                return True
        return False


# --------------------------------------------------------------------------
# Ventana
# --------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AIS + APRS para OpenCPN")
        self.resizable(False, False)
        self.cfg = cargar_config()
        self.cola = queue.Queue()
        self.motor = Motor(self.cola)
        self.v = {}

        nb = ttk.Notebook(self)
        nb.grid(padx=8, pady=8)
        t_est = ttk.Frame(nb, padding=10)
        t_ais = ttk.Frame(nb, padding=10)
        t_aprs = ttk.Frame(nb, padding=10)
        t_gen = ttk.Frame(nb, padding=10)
        nb.add(t_est, text="Estado")
        nb.add(t_ais, text="AIS")
        nb.add(t_aprs, text="APRS")
        nb.add(t_gen, text="General")

        # ---- pestaña Estado ----
        self.s = {}
        filas = [("gps", "Posición GPS", "—"), ("ais", "AIS (aisstream.io)", "Parado"),
                 ("trafico", "Tráfico AIS", "—"), ("aprs", "APRS-IS", "Parado"),
                 ("baliza", "Última baliza APRS", "—")]
        cuadro = ttk.Frame(t_est)
        cuadro.grid(row=0, column=0, sticky="w")
        for i, (k, etq, ini) in enumerate(filas):
            ttk.Label(cuadro, text=etq + ":").grid(row=i, column=0, sticky="w", padx=(0, 8))
            self.s[k] = tk.StringVar(value=ini)
            ttk.Label(cuadro, textvariable=self.s[k]).grid(row=i, column=1, sticky="w")

        bot = ttk.Frame(t_est, padding=(0, 8))
        bot.grid(row=1, column=0, sticky="w")
        self.b_ais = ttk.Button(bot, text="▶ Iniciar AIS", width=16, command=self.alternar_ais)
        self.b_ais.pack(side="left")
        self.b_aprs = ttk.Button(bot, text="▶ Iniciar APRS", width=16, command=self.alternar_aprs)
        self.b_aprs.pack(side="left", padx=4)
        self.b_ya = ttk.Button(bot, text="Enviar posición ahora", command=self.aprs_ya,
                               state="disabled")
        self.b_ya.pack(side="left")
        ttk.Button(bot, text="Abrir OpenCPN", command=self.abrir_opencpn).pack(side="left", padx=4)
        ttk.Button(bot, text="Minimizar", command=self.iconify).pack(side="left")

        self.txt = tk.Text(t_est, width=86, height=14, state="disabled", font=("Consolas", 9))
        self.txt.grid(row=2, column=0)

        # ---- pestaña AIS ----
        self._campos(t_ais, [
            ("API key aisstream.io", "api_key", 44, "clave"),
            ("Radio de la zona (millas)", "radio", 8, None),
            ("Enviar AIS a (IP)", "out_host", 16, None),
            ("Puerto AIS → OpenCPN (UDP)", "out_port", 8, None),
            ("MMSI a ignorar (el propio)", "excluir", 24, None),
        ], [("test", "Barco de prueba 'PRUEBA SCRIPT' 1 milla al norte"),
            ("ais_auto", "Iniciar AIS automáticamente al abrir")])

        # ---- pestaña APRS ----
        fila = self._campos(t_aprs, [
            ("Indicativo con SSID", "indicativo", 14, "passcode_btn"),
            ("Passcode APRS-IS", "passcode", 10, None),
            ("Servidor APRS-IS", "servidor", 24, None),
            ("Puerto servidor", "puerto", 8, None),
            ("Símbolo (/Y velero, /s barco)", "simbolo", 6, None),
            ("Comentario", "comentario", 40, None),
            ("Baliza navegando cada (min)", "int_movimiento", 6, None),
            ("Baliza parado cada (min)", "int_parado", 6, None),
            ("Baliza extra si cambia el rumbo (°)", "giro", 6, None),
        ], [("aprs_auto", "Iniciar APRS automáticamente al abrir")])

        # ---- pestaña General ----
        fila = self._campos(t_gen, [
            ("Puerto GPS ← OpenCPN (UDP, RMC)", "gps_port", 8, None),
            ("Latitud inicial (sin GPS)", "lat", 12, None),
            ("Longitud inicial (sin GPS)", "lon", 12, None),
            ("Ruta de OpenCPN", "ruta_opencpn", 44, "buscar"),
        ], [("abrir_opencpn", "Abrir OpenCPN al abrir esta app")])
        ttk.Label(t_gen, text="El mismo puerto GPS sirve para el AIS y el APRS.\n"
                              "En OpenCPN: salida de red UDP 127.0.0.1 a este puerto, filtro RMC.",
                  foreground="gray").grid(row=fila, column=0, columnspan=3, sticky="w", pady=(8, 0))

        abajo = ttk.Frame(self, padding=(8, 0, 8, 8))
        abajo.grid(row=1, column=0, sticky="ew")
        ttk.Button(abajo, text="Guardar configuración", command=self.guardar).pack(side="left")
        ttk.Label(abajo, text="(los cambios se aplican al reiniciar cada servicio)",
                  foreground="gray").pack(side="left", padx=6)

        self.protocol("WM_DELETE_WINDOW", self.cerrar)
        self.after(300, self.revisar_cola)

        self.motor.abrir_gps(self.cfg["gps_port"])
        if self.cfg.get("abrir_opencpn"):
            self.abrir_opencpn()
        if self.cfg.get("ais_auto") and self.cfg.get("api_key"):
            self.after(500, self.alternar_ais)
        if self.cfg.get("aprs_auto") and self.cfg.get("passcode"):
            self.after(700, self.alternar_aprs)

    def _campos(self, padre, campos, casillas):
        fila = 0
        for etq, clave, ancho, extra in campos:
            ttk.Label(padre, text=etq).grid(row=fila, column=0, sticky="w", pady=1)
            var = tk.StringVar(value=str(self.cfg[clave]))
            ent = ttk.Entry(padre, textvariable=var, width=ancho,
                            show="•" if extra == "clave" else "")
            ent.grid(row=fila, column=1, sticky="w", pady=1)
            self.v[clave] = var
            if extra == "clave":
                ver = tk.BooleanVar(value=False)
                ttk.Checkbutton(padre, text="ver", variable=ver,
                                command=lambda e=ent, b=ver: e.config(show="" if b.get() else "•")
                                ).grid(row=fila, column=2, sticky="w")
            elif extra == "passcode_btn":
                ttk.Button(padre, text="Calcular passcode", command=self.calcular_passcode
                           ).grid(row=fila, column=2, sticky="w", padx=4)
            elif extra == "buscar":
                ttk.Button(padre, text="…", width=3, command=self.buscar_opencpn
                           ).grid(row=fila, column=2, sticky="w")
            fila += 1
        for clave, texto in casillas:
            var = tk.BooleanVar(value=bool(self.cfg[clave]))
            ttk.Checkbutton(padre, text=texto, variable=var).grid(
                row=fila, column=0, columnspan=3, sticky="w")
            self.v[clave] = var
            fila += 1
        return fila

    # --- configuración ---
    def leer(self):
        cfg = {k: v.get() for k, v in self.v.items()}
        cfg["indicativo"] = cfg["indicativo"].strip().upper()
        cfg["simbolo"] = (cfg["simbolo"].strip() + "  ")[:2]
        try:
            for k in ("lat", "lon", "radio", "int_movimiento", "int_parado", "giro"):
                cfg[k] = float(str(cfg[k]).replace(",", "."))
            for k in ("gps_port", "out_port", "puerto"):
                cfg[k] = int(cfg[k])
        except ValueError:
            messagebox.showerror("Error", "Revisa los campos numéricos (posición, radio, "
                                          "puertos, intervalos).")
            return None
        if cfg["int_movimiento"] < 1 or cfg["int_parado"] < 5:
            messagebox.showerror("Error", "APRS: mínimo 1 min navegando y 5 min parado.")
            return None
        return cfg

    def guardar(self):
        cfg = self.leer()
        if not cfg:
            return None
        if cfg["gps_port"] != self.cfg["gps_port"]:
            self.motor.abrir_gps(cfg["gps_port"])
        self.cfg = cfg
        guardar_config(cfg)
        self.log("Configuración guardada")
        return cfg

    def calcular_passcode(self):
        ind = self.v["indicativo"].get().strip()
        if ind:
            self.v["passcode"].set(str(passcode(ind)))

    def buscar_opencpn(self):
        ruta = filedialog.askopenfilename(title="Selecciona opencpn.exe",
                                          filetypes=[("Programa", "*.exe"), ("Todos", "*.*")])
        if ruta:
            self.v["ruta_opencpn"].set(ruta)

    def abrir_opencpn(self):
        ruta = self.v["ruta_opencpn"].get()
        try:
            subprocess.Popen([ruta], cwd=os.path.dirname(ruta) or None)
            self.log("Abriendo OpenCPN")
        except OSError as ex:
            self.log(f"No se pudo abrir OpenCPN ({ex}). Revisa la ruta en General.")

    # --- servicios ---
    def alternar_ais(self):
        if self.motor.ais_activo():
            self.motor.parar_ais()
            self.b_ais.config(state="disabled")
            return
        cfg = self.guardar()
        if not cfg:
            return
        if not cfg["api_key"].strip():
            messagebox.showwarning("Falta la API key", "Introdúcela en la pestaña AIS.")
            return
        self.motor.iniciar_ais(cfg)
        self.b_ais.config(text="■ Parar AIS")

    def alternar_aprs(self):
        if self.motor.aprs_activo():
            self.motor.parar_aprs()
            self.b_aprs.config(state="disabled")
            return
        cfg = self.guardar()
        if not cfg:
            return
        if not str(cfg["passcode"]).strip():
            messagebox.showwarning("Falta el passcode", "Rellénalo en la pestaña APRS.")
            return
        self.motor.iniciar_aprs(cfg)
        self.b_aprs.config(text="■ Parar APRS")
        self.b_ya.config(state="normal")

    def aprs_ya(self):
        self.motor.aprs_ya()
        self.log("Baliza APRS solicitada (se envía en unos segundos si hay GPS)")

    # --- ventana ---
    def log(self, t):
        if not t[:2].isdigit():
            t = f"{time.strftime('%H:%M:%S')}  {t}"
        self.txt.config(state="normal")
        self.txt.insert("end", t + "\n")
        n = int(self.txt.index("end-1c").split(".")[0])
        if n > 400:
            self.txt.delete("1.0", f"{n - 400}.0")
        self.txt.see("end")
        self.txt.config(state="disabled")

    def revisar_cola(self):
        try:
            while True:
                tipo, dato = self.cola.get_nowait()
                if tipo == "log":
                    self.log(dato)
                elif tipo == "estado":
                    for k, val in dato.items():
                        self.s[k].set(val)
                elif tipo == "fin" and dato == "ais":
                    self.b_ais.config(text="▶ Iniciar AIS", state="normal")
                elif tipo == "fin" and dato == "aprs":
                    self.b_aprs.config(text="▶ Iniciar APRS", state="normal")
                    self.b_ya.config(state="disabled")
        except queue.Empty:
            pass
        self.after(300, self.revisar_cola)

    def cerrar(self):
        if (self.motor.ais_activo() or self.motor.aprs_activo()) and not messagebox.askyesno(
                "Salir", "Hay servicios funcionando. ¿Parar y salir?"):
            return
        self.motor.parar_ais()
        self.motor.parar_aprs()
        self.after(500, self.destroy)


if __name__ == "__main__":
    App().mainloop()
