#!/usr/bin/env python3
"""
aisstream.io -> OpenCPN
=======================
Descarga AIS en tiempo real de aisstream.io para la zona alrededor del barco,
lo convierte a sentencias NMEA !AIVDM y lo envía por UDP a OpenCPN.

Requisitos (en el PC del barco):
    pip install websockets

Uso básico (zona fija):
    python aisstream_opencpn.py --key TU_API_KEY --lat 36.6 --lon -6.3

Uso siguiendo al barco (OpenCPN le envía la posición GPS por UDP):
    python aisstream_opencpn.py --key TU_API_KEY --lat 36.6 --lon -6.3 --gps-port 10112

Configuración en OpenCPN (Opciones -> Conexiones -> Añadir conexión):
  1) Entrada AIS:  Red, protocolo UDP, dirección 127.0.0.1, puerto 10111, "Recibir datos".
  2) (Opcional, para seguir al barco) Salida GPS: Red, UDP, 127.0.0.1, puerto 10112,
     marcar "Enviar datos" y filtrar salida solo a RMC.
"""

import argparse
import asyncio
import json
import math
import socket
import sys
import time

AISSTREAM_URL = "wss://stream.aisstream.io/v0/stream"
TIPOS = [
    "PositionReport",                 # tipos 1/2/3 (clase A)
    "StandardClassBPositionReport",   # tipo 18 (clase B)
    "ExtendedClassBPositionReport",   # tipo 19
    "ShipStaticData",                 # tipo 5 (nombre, destino... clase A)
    "StaticDataReport",               # tipo 24 (nombre clase B)
]

# --------------------------------------------------------------------------
# Codificador AIS -> NMEA (sin dependencias externas)
# --------------------------------------------------------------------------

class Bits:
    def __init__(self):
        self.b = []

    def u(self, value, n):
        value = int(value) & ((1 << n) - 1)
        self.b.extend((value >> (n - 1 - i)) & 1 for i in range(n))

    def s(self, value, n):  # entero con signo (complemento a 2)
        self.u(int(value) & ((1 << n) - 1), n)

    def t(self, text, nchars):  # texto 6 bits, relleno con '@'
        text = (text or "").upper()[:nchars].ljust(nchars, "@")
        for ch in text:
            c = ord(ch)
            if 64 <= c <= 95:
                v = c - 64
            elif 32 <= c <= 63:
                v = c
            else:
                v = 0  # carácter no válido -> '@'
            self.u(v, 6)

    def payload(self):
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


def nmea(bits, canal="A"):
    """Devuelve la lista de sentencias !AIVDM (troceando si hace falta)."""
    global _seq
    payload, fill = bits.payload()
    trozos = [payload[i:i + 60] for i in range(0, len(payload), 60)] or [""]
    total = len(trozos)
    seq = ""
    if total > 1:
        seq = str(_seq)
        _seq = (_seq + 1) % 10
    frases = []
    for i, trozo in enumerate(trozos, 1):
        f = fill if i == total else 0
        cuerpo = f"AIVDM,{total},{i},{seq},{canal},{trozo},{f}"
        cs = 0
        for ch in cuerpo:
            cs ^= ord(ch)
        frases.append(f"!{cuerpo}*{cs:02X}")
    return frases


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


def enc_tipo1(mmsi, m):
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


def enc_tipo18(mmsi, m):
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


def enc_tipo19(mmsi, m):
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


def enc_tipo5(mmsi, m):
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


def enc_tipo24(mmsi, m):
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


def convertir(msg):
    """JSON de aisstream -> lista de sentencias NMEA."""
    tipo = msg.get("MessageType")
    cuerpo = (msg.get("Message") or {}).get(tipo) or {}
    mmsi = cuerpo.get("UserID") or (msg.get("MetaData") or {}).get("MMSI")
    if not mmsi:
        return []
    if tipo == "PositionReport":
        bloques = [enc_tipo1(mmsi, cuerpo)]
    elif tipo == "StandardClassBPositionReport":
        bloques = [enc_tipo18(mmsi, cuerpo)]
    elif tipo == "ExtendedClassBPositionReport":
        bloques = [enc_tipo19(mmsi, cuerpo)]
    elif tipo == "ShipStaticData":
        bloques = [enc_tipo5(mmsi, cuerpo)]
    elif tipo == "StaticDataReport":
        bloques = enc_tipo24(mmsi, cuerpo)
    else:
        return []
    frases = []
    for b in bloques:
        frases.extend(nmea(b))
    return frases


# --------------------------------------------------------------------------
# Posición propia (opcional, desde OpenCPN por UDP) y zona de búsqueda
# --------------------------------------------------------------------------

def caja(lat, lon, radio_mn):
    dlat = radio_mn / 60.0
    dlon = radio_mn / (60.0 * max(math.cos(math.radians(lat)), 0.01))
    return [[lat - dlat, lon - dlon], [lat + dlat, lon + dlon]]


def distancia_mn(lat1, lon1, lat2, lon2):
    dlat = (lat2 - lat1) * 60
    dlon = (lon2 - lon1) * 60 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dlat, dlon)


def parse_rmc(linea):
    try:
        campos = linea.split("*")[0].split(",")
        if not campos[0].endswith("RMC") or campos[2] != "A":
            return None
        lat = int(campos[3][:2]) + float(campos[3][2:]) / 60
        if campos[4] == "S":
            lat = -lat
        lon = int(campos[5][:3]) + float(campos[5][3:]) / 60
        if campos[6] == "W":
            lon = -lon
        return lat, lon
    except (IndexError, ValueError):
        return None


class Estado:
    def __init__(self, lat, lon, radio):
        self.lat, self.lon, self.radio = lat, lon, radio
        self.sub_lat, self.sub_lon = lat, lon
        self.mover = asyncio.Event()
        self.gps_ok = False


async def barco_prueba(estado, salida, destino):
    """Blanco ficticio 1 milla al norte del barco, para comprobar OpenCPN."""
    while True:
        m = {"Latitude": estado.lat + 1 / 60, "Longitude": estado.lon,
             "Sog": 0, "Cog": 0, "TrueHeading": 0, "NavigationalStatus": 1,
             "Timestamp": int(time.time()) % 60}
        frases = nmea(enc_tipo1(999000001, m))
        frases += nmea(enc_tipo5(999000001, {"Name": "PRUEBA SCRIPT", "Type": 37}))
        for f in frases:
            salida.sendto((f + "\r\n").encode(), destino)
        await asyncio.sleep(10)


class GpsUDP(asyncio.DatagramProtocol):
    def __init__(self, estado):
        self.e = estado

    def datagram_received(self, data, addr):
        for linea in data.decode(errors="ignore").splitlines():
            pos = parse_rmc(linea.strip())
            if pos:
                if not self.e.gps_ok:
                    print(f"GPS recibido de OpenCPN: {pos[0]:.4f}, {pos[1]:.4f}")
                    self.e.gps_ok = True
                self.e.lat, self.e.lon = pos
                # Si nos hemos alejado 1/3 del radio, recentrar la zona
                if distancia_mn(self.e.sub_lat, self.e.sub_lon, *pos) > self.e.radio / 3:
                    self.e.mover.set()


# --------------------------------------------------------------------------
# Bucle principal
# --------------------------------------------------------------------------

async def ejecutar(args):
    import websockets  # pip install websockets

    estado = Estado(args.lat, args.lon, args.radio)
    salida = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    destino = (args.out_host, args.out_port)

    if args.gps_port:
        loop = asyncio.get_running_loop()
        await loop.create_datagram_endpoint(
            lambda: GpsUDP(estado), local_addr=("0.0.0.0", args.gps_port))
        print(f"Escuchando GPS de OpenCPN en UDP {args.gps_port}")

    if args.test:
        asyncio.create_task(barco_prueba(estado, salida, destino))
        print("Enviando barco de prueba 'PRUEBA SCRIPT' 1 milla al norte cada 10 s")

    nombres = {}

    espera = 5
    while True:
        estado.sub_lat, estado.sub_lon = estado.lat, estado.lon
        estado.mover.clear()
        zona = caja(estado.sub_lat, estado.sub_lon, estado.radio)
        sub = {"APIKey": args.key, "BoundingBoxes": [zona],
               "FilterMessageTypes": TIPOS}
        try:
            async with websockets.connect(AISSTREAM_URL, ping_interval=20,
                                          open_timeout=args.timeout) as ws:
                await ws.send(json.dumps(sub))  # hay que enviarla en < 3 s
                print(f"Conectado. Zona {estado.radio:.0f} MN alrededor de "
                      f"{estado.sub_lat:.4f}, {estado.sub_lon:.4f}")
                espera = 5
                n, t0, barcos = 0, time.time(), set()

                async def leer():
                    nonlocal n, t0, barcos
                    async for raw in ws:
                        msg = json.loads(raw)
                        if "error" in msg:
                            print("Error de aisstream:", msg["error"])
                            return
                        meta = msg.get("MetaData") or {}
                        mmsi = meta.get("MMSI")
                        if mmsi in args.excluir:
                            continue
                        nombre = (meta.get("ShipName") or "").strip()
                        if nombre:
                            nombres[mmsi] = nombre
                        for frase in convertir(msg):
                            salida.sendto((frase + "\r\n").encode(), destino)
                            n += 1
                            if args.verbose:
                                print("   ", frase)
                        barcos.add(mmsi)
                        if args.diag and meta.get("latitude") is not None:
                            d = distancia_mn(estado.lat, estado.lon,
                                             meta["latitude"], meta["longitude"])
                            print(f"{time.strftime('%H:%M:%S')}  {msg.get('MessageType'):30s} "
                                  f"{mmsi}  {nombres.get(mmsi, ''):20s} a {d:5.1f} MN")
                        if time.time() - t0 > 60:
                            print(f"{time.strftime('%H:%M:%S')}  {n} sentencias de "
                                  f"{len(barcos)} barcos en el último minuto")
                            n, t0, barcos = 0, time.time(), set()

                lector = asyncio.create_task(leer())
                movido = asyncio.create_task(estado.mover.wait())
                hecho, _ = await asyncio.wait(
                    {lector, movido}, return_when=asyncio.FIRST_COMPLETED)
                for t in (lector, movido):
                    t.cancel()
                if movido in hecho:
                    print("El barco se ha movido: recentrando zona...")
                    continue
                if lector.done() and not lector.cancelled() and lector.exception():
                    raise lector.exception()
                print("aisstream ha cerrado la conexión.")
        except Exception as ex:  # red caída, sin cobertura, etc.
            print(f"Conexión perdida ({ex}). Reintento en {espera} s")
        await asyncio.sleep(espera)
        espera = min(espera * 2, 120)


def main():
    p = argparse.ArgumentParser(description="aisstream.io -> OpenCPN (NMEA AIVDM por UDP)")
    p.add_argument("--key", required=True, help="API key de aisstream.io")
    p.add_argument("--lat", type=float, required=True, help="latitud inicial (grados decimales)")
    p.add_argument("--lon", type=float, required=True, help="longitud inicial (grados decimales, E positivo)")
    p.add_argument("--radio", type=float, default=30, help="radio de la zona en millas (por defecto 30)")
    p.add_argument("--out-host", default="127.0.0.1", help="destino UDP (por defecto 127.0.0.1)")
    p.add_argument("--out-port", type=int, default=10111, help="puerto UDP hacia OpenCPN (por defecto 10111)")
    p.add_argument("--gps-port", type=int, default=0, help="puerto UDP donde OpenCPN envía RMC (0 = zona fija)")
    p.add_argument("--excluir", type=int, nargs="*", default=[], help="MMSI a ignorar (el propio)")
    p.add_argument("--timeout", type=float, default=60, help="segundos de espera al conectar (por defecto 60)")
    p.add_argument("--diag", action="store_true", help="mostrar cada barco recibido y su distancia")
    p.add_argument("--test", action="store_true", help="enviar un barco ficticio a 1 milla para probar OpenCPN")
    p.add_argument("-v", "--verbose", action="store_true", help="mostrar cada sentencia")
    args = p.parse_args()
    try:
        asyncio.run(ejecutar(args))
    except KeyboardInterrupt:
        print("\nParado.")


if __name__ == "__main__":
    main()
