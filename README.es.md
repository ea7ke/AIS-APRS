# AIS-APRS para OpenCPN

*[Read in English](README.md)*

Aplicación de escritorio (Python + Tkinter) que añade dos servicios por internet a [OpenCPN](https://opencpn.org), usando los datos GPS que el propio OpenCPN envía por la red:

- **AIS por internet**: descarga en tiempo real los barcos cercanos desde [aisstream.io](https://aisstream.io) y los entrega a OpenCPN como sentencias NMEA `!AIVDM` estándar. Así aparecen en la carta igual que los de un receptor normal.
- **Posición por APRS**: publica la posición del barco (con rumbo y velocidad) en la red APRS-IS, para que se pueda seguir en [aprs.fi](https://aprs.fi) y webs similares. *Requiere licencia de radioaficionado.*

Está pensada para barcos que solo tienen receptor AIS (o ninguno) y quieren ver el tráfico más allá del alcance de su antena, y que les puedan seguir desde tierra.

> ⚠️ El AIS por internet llega con retrasos variables y solo muestra los barcos que capta alguna estación costera de aisstream. **No sustituye a un receptor AIS ni a la vigilancia visual, y nunca debe usarse para evitar abordajes.**

## Funciones

- **La zona de AIS se vuelve a centrar sola** al moverse el barco.
- **Prioridad del VHF**: si tu receptor AIS entra en OpenCPN, los barcos oídos por radio se ignoran de internet durante un tiempo configurable (5 min por defecto). Así no saltan entre la posición real y la retrasada.
- **Filtro de datos antiguos**: se descartan los datos de internet con más de cierta antigüedad (120 s por defecto). La antigüedad se mide con la hora del GPS, así que da igual si el reloj del PC está mal.
- **Los barcos de internet van marcados**: OpenCPN no permite colorearlos según su origen, así que su nombre lleva un sufijo, por defecto `NEPTUNE THELIS (NET)`.
- **Balizas APRS**: a intervalos distintos navegando y parados, más una baliza extra en los cambios grandes de rumbo. No se envía nada sin una posición GPS reciente.
- **Interfaz en español e inglés**, que se elige en la propia app (por defecto, automático).
- **Barco de prueba** integrado para comprobar la conexión con OpenCPN.

## Archivos

| Archivo | Qué es |
|---|---|
| `opencpn_net.pyw` | La aplicación con ventana (AIS + APRS). Se abre con doble clic, sin consola. |
| `aisstream_opencpn.py` | Codificador AIS y filtro que combina internet y VHF. La app lo necesita en la misma carpeta. También funciona solo, desde la línea de comandos. |
| `i18n.py` | Traducciones (inglés / español). |

## Instalación

1. Instala [Python 3](https://www.python.org/downloads/) (en Windows marca *Add python.exe to PATH*).
2. Instala la única dependencia (solo hace falta para el AIS):
   ```
   pip install -r requirements.txt
   ```
3. Pon los tres archivos `.py`/`.pyw` en la misma carpeta.
4. Consigue una API key gratuita en [aisstream.io](https://aisstream.io) (se entra con la cuenta de GitHub).

## Configuración de OpenCPN

En *Opciones → Conexiones → Añadir conexión*:

| Conexión | Tipo | Dirección | Puerto | Sentido |
|---|---|---|---|---|
| Entrada AIS | Red, UDP | `127.0.0.1` | `10111` | Recibir datos |
| Salida de datos | Red, UDP | `127.0.0.1` | `10112` | Enviar datos, filtro de salida **RMC** y **VDM** |

- **RMC** da la posición, que sirve para centrar la zona de AIS y para el APRS.
- **VDM** reenvía los barcos que recibe tu VHF y solo hace falta para la **prioridad del VHF**. Para eso, tu receptor AIS tiene que ser ya una entrada de OpenCPN, por ejemplo por un puerto serie NMEA 0183 o por una pasarela NMEA 2000. La app reconoce e ignora sus propios datos de internet si OpenCPN se los devuelve.

## Uso

Abre `opencpn_net.pyw`. Tiene cuatro pestañas:

- **Estado**: posición GPS, estado del AIS y del APRS, tráfico recibido (con cuántos datos se han descartado por antiguos o por oírse por VHF), última baliza y un registro de mensajes. Tiene botones para iniciar o parar cada servicio y para *Enviar posición ahora*.
- **AIS**: API key, radio de la zona en millas, IP y puerto de salida hacia OpenCPN, MMSI a ignorar (el propio), antigüedad máxima de los datos, tiempo de prioridad del VHF, sufijo para el nombre de los barcos de internet y barco de prueba.
- **APRS**: indicativo con SSID (`-8` es lo habitual en barcos), passcode APRS-IS (con botón para calcularlo), servidor, símbolo (`/Y` velero, `/s` barco), comentario e intervalos de baliza.
- **General**: idioma, puerto de datos, posición inicial (se usa hasta que llegan datos del GPS), ruta de OpenCPN y opción de abrirlo al arrancar.

Si marcas *Iniciar … automáticamente* y *Abrir OpenCPN al abrir esta app*, un solo acceso directo lo arranca todo. Si el doble clic usa otra instalación de Python sin `websockets`, crea un acceso directo que apunte explícitamente a la correcta:

```
"C:\...\Python313\pythonw.exe" "C:\...\opencpn_net.pyw"
```

La configuración se guarda en `opencpn_net_config.json`, junto a la app. Ese archivo contiene la API key y el passcode, así que **no se sube al repositorio** (está en `.gitignore`). La configuración de la versión 1.0 se importa sola.

### Balizas APRS

Ejemplo de paquete:

```
N0CALL-8>APRS,TCPIP*:!3636.00N/00618.00WY200/005OpenCPN AIS-APRS
```

Lleva la posición, el símbolo, el rumbo sobre el fondo y la velocidad en nudos (solo navegando, a partir de 0,5 kn) y el comentario.

- Navegando (≥ 1 kn): cada *N* minutos (5 por defecto), más una baliza extra si el rumbo cambia 30° o más (como mucho una por minuto).
- Parado: cada 30 minutos.
- No se envía nada si el GPS lleva 2 minutos sin dar datos.
- Los intervalos mínimos son 1 min navegando y 5 min parado, para no saturar la red APRS.

## Línea de comandos (solo AIS)

```
python aisstream_opencpn.py --key TU_API_KEY --lat 36.6 --lon -6.3 --radius 30 --gps-port 10112 --lang es
```

Opciones útiles: `--max-age`, `--rf-hold`, `--tag`, `--diag` (muestra cada barco recibido y su distancia), `--test` (barco ficticio de prueba), `-v` (muestra cada sentencia NMEA) y `--lang es|en`. Ver `--help`.

## Tipos de mensaje AIS soportados

1, 2 y 3 (posición clase A), 5 (datos estáticos clase A), 18 y 19 (posición clase B) y 24 (datos estáticos clase B). El codificador AIS no tiene dependencias externas.

## Proyectos relacionados

- [AisLive_pi](https://github.com/Kupofty/AisLive_pi): plugin de OpenCPN que muestra AIS por internet desde OpenWaters.io.

## Licencia

Copyright (C) 2026 EA7KE. Distribuido bajo la [GNU General Public License v2.0 o posterior](LICENSE) (GPL-2.0-or-later), la misma familia de licencia que OpenCPN.
