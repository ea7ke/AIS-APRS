# AIS-APRS para OpenCPN

Aplicación de escritorio (Python + Tkinter) que amplía [OpenCPN](https://opencpn.org) con dos servicios por internet, usando la posición GPS que el propio OpenCPN envía por la red:

- **AIS por internet**: descarga en tiempo real los barcos cercanos desde [aisstream.io](https://aisstream.io) y los entrega a OpenCPN como sentencias NMEA `!AIVDM`. Así aparecen en la carta igual que los de un receptor AIS.
- **Posición por APRS-IS**: publica la posición del barco (con rumbo y velocidad) en la red APRS-IS, para que se vea en [aprs.fi](https://aprs.fi) y webs similares. *Requiere licencia de radioaficionado.*

Pensado para barcos que solo tienen receptor AIS (o ninguno) y quieren ver el tráfico más allá del alcance de su antena, y que les puedan seguir desde tierra.

> ⚠️ El AIS por internet llega con retraso y solo muestra los barcos que capta alguna estación costera de aisstream. **No sustituye a un receptor AIS ni a la vigilancia visual** para evitar abordajes.

## Archivos

| Archivo | Qué es |
|---|---|
| `opencpn_net.pyw` | La aplicación con ventana (AIS + APRS). Se abre con doble clic, sin consola. |
| `aisstream_opencpn.py` | Codificador AIS → NMEA. La app lo necesita en la misma carpeta. También funciona solo, desde la línea de comandos. |

## Instalación

1. Instala [Python 3](https://www.python.org/downloads/) (en Windows marca *Add python.exe to PATH*).
2. Instala la única dependencia (solo para el AIS):
   ```
   pip install -r requirements.txt
   ```
3. Copia los dos archivos en la misma carpeta.
4. Consigue una API key gratuita en [aisstream.io](https://aisstream.io) (se entra con la cuenta de GitHub).

## Configuración de OpenCPN

En *Opciones → Conexiones → Añadir conexión*:

| Conexión | Tipo | Dirección | Puerto | Dirección del dato |
|---|---|---|---|---|
| Entrada AIS | Red, UDP | `127.0.0.1` | `10111` | Recibir datos |
| Salida GPS | Red, UDP | `127.0.0.1` | `10112` | Enviar datos, filtro de salida **RMC** |

El mismo puerto GPS lo usan el AIS (para centrar la zona de búsqueda en el barco) y el APRS.

## Uso

Abre `opencpn_net.pyw`. Tiene cuatro pestañas:

- **Estado**: posición GPS, estado de AIS y APRS, tráfico recibido, última baliza y un registro de mensajes. Botones para iniciar o parar cada servicio y para *Enviar posición ahora*.
- **AIS**: API key, radio de la zona en millas, IP y puerto de salida hacia OpenCPN, MMSI a ignorar (el propio) y un *barco de prueba* ficticio a 1 milla para comprobar que OpenCPN recibe los datos.
- **APRS**: indicativo con SSID (`-8` es lo habitual para barcos), passcode APRS-IS (con botón para calcularlo), servidor, símbolo (`/Y` velero, `/s` barco), comentario e intervalos de baliza.
- **General**: puerto GPS, posición inicial (mientras no hay GPS), ruta de OpenCPN y opción de abrirlo al arrancar.

Con *Iniciar … automáticamente* y *Abrir OpenCPN al abrir esta app* marcados, un único acceso directo lo arranca todo. Si el doble clic usa otra instalación de Python sin `websockets`, crea un acceso directo que apunte explícitamente a ella:

```
"C:\...\Python313\pythonw.exe" "C:\...\opencpn_net.pyw"
```

La configuración se guarda en `opencpn_net_config.json`, junto a la app. Ese archivo contiene la API key y el passcode y **no se sube al repositorio** (está en `.gitignore`).

### Balizas APRS

Ejemplo de paquete enviado:

```
N0CALL-8>APRS,TCPIP*:!3636.00N/00618.00WY200/005OpenCPN AIS-APRS
```

- Navegando (≥ 1 kn): cada *N* minutos (5 por defecto), más una baliza extra si el rumbo cambia ≥ 30° (como mucho una por minuto).
- Parado: cada 30 minutos.
- No se envía nada si el GPS lleva más de 2 minutos sin dar datos.
- Mínimos forzados de 1 min navegando y 5 min parado, para no saturar la red APRS.

## Uso por línea de comandos (solo AIS)

```
python aisstream_opencpn.py --key TU_API_KEY --lat 36.6 --lon -6.3 --radio 30 --gps-port 10112
```

Opciones útiles: `--diag` (muestra cada barco recibido y su distancia), `--test` (barco ficticio de prueba), `-v` (muestra cada sentencia NMEA), `--timeout` (espera al conectar). Ver `--help`.

## Tipos AIS soportados

1, 2 y 3 (posición clase A), 5 (datos estáticos clase A), 18 y 19 (posición clase B) y 24 (datos estáticos clase B). El codificador AIS está implementado sin dependencias externas.

## Licencia

Copyright (C) 2026 EA7KE. Distribuido bajo la [GNU General Public License v2.0 o posterior](LICENSE) (GPL-2.0-or-later), la misma familia de licencia que OpenCPN.
