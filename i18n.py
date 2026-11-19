# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EA7KE
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License, version 2 or (at your option)
# any later version. It is distributed WITHOUT ANY WARRANTY. See LICENSE.
"""
Minimal translation support (English / Spanish).

Usage:
    from i18n import t, set_language
    set_language("es")          # "en", "es" or "auto"
    t("ais_connected", radius=30, lat=36.6, lon=-6.3)

To add a language, add its code to LANGUAGES and a translation for every key
in STRINGS. Missing translations fall back to English.
"""

import locale

LANGUAGES = {"en": "English", "es": "Español"}

STRINGS = {
    # ---------------------------------------------------------------- common
    "gps_listening": {
        "en": "Listening for OpenCPN data (RMC/VDM) on UDP {port}",
        "es": "Escuchando datos de OpenCPN (RMC/VDM) en UDP {port}",
    },
    "gps_first_fix": {
        "en": "Position received from OpenCPN: {lat:.4f}, {lon:.4f}",
        "es": "Posición recibida de OpenCPN: {lat:.4f}, {lon:.4f}",
    },
    "gps_port_error": {
        "en": "Cannot open UDP port {port}: {err}",
        "es": "No puedo abrir el puerto UDP {port}: {err}",
    },
    "conn_lost": {
        "en": "Connection lost ({err}). Retrying in {s} s",
        "es": "Conexión perdida ({err}). Reintento en {s} s",
    },
    "stopped": {"en": "Stopped", "es": "Parado"},
    "connecting": {"en": "Connecting…", "es": "Conectando…"},
    "reconnecting": {"en": "Reconnecting…", "es": "Reconectando…"},
    "connected": {"en": "Connected", "es": "Conectado"},

    # ------------------------------------------------------------------- AIS
    "ais_connected": {
        "en": "Connected. Area of {radius:.0f} NM around {lat:.4f}, {lon:.4f}",
        "es": "Conectado. Zona de {radius:.0f} MN alrededor de {lat:.4f}, {lon:.4f}",
    },
    "ais_moved": {
        "en": "Vessel has moved: re-centring the area",
        "es": "El barco se ha movido: recentrando la zona",
    },
    "ais_closed": {
        "en": "aisstream closed the connection",
        "es": "aisstream ha cerrado la conexión",
    },
    "ais_error": {"en": "aisstream error: {err}", "es": "Error de aisstream: {err}"},
    "ais_test": {
        "en": "Sending test vessel 'TEST TARGET' 1 NM north every 10 s",
        "es": "Enviando barco de prueba 'TEST TARGET' 1 milla al norte cada 10 s",
    },
    "ais_minute": {
        "en": "{vessels} vessels, {sent} sentences sent; dropped: {old} old, {rf} heard on VHF (last minute)",
        "es": "{vessels} barcos, {sent} sentencias enviadas; descartados: {old} antiguos, {rf} oídos por VHF (último minuto)",
    },
    "ais_no_websockets": {
        "en": "The 'websockets' library is missing (pip install websockets)",
        "es": "Falta la librería 'websockets' (pip install websockets)",
    },
    "ais_rf_first": {
        "en": "VHF AIS data received from OpenCPN: VHF targets take priority",
        "es": "Recibiendo AIS por VHF desde OpenCPN: los blancos de radio tienen prioridad",
    },
    "ais_diag": {
        "en": "{type:30s} {mmsi}  {name:20s} at {dist:5.1f} NM",
        "es": "{type:30s} {mmsi}  {name:20s} a {dist:5.1f} MN",
    },

    # ------------------------------------------------------------------ APRS
    "aprs_sent": {"en": "Sent: {frame}", "es": "Enviado: {frame}"},
    "aprs_server_closed": {
        "en": "the server closed the connection",
        "es": "el servidor cerró la conexión",
    },
    "aprs_connected_to": {"en": "Connected to {server}", "es": "Conectado a {server}"},
    "aprs_unverified": {
        "en": " (NOT VERIFIED: check the passcode)",
        "es": " (SIN VERIFICAR: revisa el passcode)",
    },
    "aprs_beacon_requested": {
        "en": "APRS beacon requested (sent within a few seconds if GPS is available)",
        "es": "Baliza APRS solicitada (se envía en unos segundos si hay GPS)",
    },

    # ------------------------------------------------------------------- GUI
    "app_title": {"en": "AIS + APRS for OpenCPN", "es": "AIS + APRS para OpenCPN"},
    "tab_status": {"en": "Status", "es": "Estado"},
    "tab_ais": {"en": "AIS", "es": "AIS"},
    "tab_aprs": {"en": "APRS", "es": "APRS"},
    "tab_general": {"en": "General", "es": "General"},
    "st_gps": {"en": "GPS position", "es": "Posición GPS"},
    "st_ais": {"en": "AIS (aisstream.io)", "es": "AIS (aisstream.io)"},
    "st_traffic": {"en": "AIS traffic", "es": "Tráfico AIS"},
    "st_aprs": {"en": "APRS-IS", "es": "APRS-IS"},
    "st_beacon": {"en": "Last APRS beacon", "es": "Última baliza APRS"},
    "st_gps_waiting": {"en": "waiting for data on UDP {port}…", "es": "esperando datos en UDP {port}…"},
    "st_gps_none": {"en": "no GPS (port 0)", "es": "sin GPS (puerto 0)"},
    "st_gps_error": {"en": "ERROR on port {port}", "es": "ERROR en puerto {port}"},
    "st_gps_value": {
        "en": "{lat:.4f}, {lon:.4f}   {sog:.1f} kn   ({time})",
        "es": "{lat:.4f}, {lon:.4f}   {sog:.1f} kn   ({time})",
    },
    "btn_start_ais": {"en": "▶ Start AIS", "es": "▶ Iniciar AIS"},
    "btn_stop_ais": {"en": "■ Stop AIS", "es": "■ Parar AIS"},
    "btn_start_aprs": {"en": "▶ Start APRS", "es": "▶ Iniciar APRS"},
    "btn_stop_aprs": {"en": "■ Stop APRS", "es": "■ Parar APRS"},
    "btn_beacon_now": {"en": "Send position now", "es": "Enviar posición ahora"},
    "btn_open_opencpn": {"en": "Open OpenCPN", "es": "Abrir OpenCPN"},
    "btn_minimize": {"en": "Minimize", "es": "Minimizar"},
    "btn_save": {"en": "Save settings", "es": "Guardar configuración"},
    "btn_calc_passcode": {"en": "Calculate passcode", "es": "Calcular passcode"},
    "save_hint": {
        "en": "(changes apply when each service is restarted)",
        "es": "(los cambios se aplican al reiniciar cada servicio)",
    },
    "show": {"en": "show", "es": "ver"},
    # AIS tab
    "f_api_key": {"en": "aisstream.io API key", "es": "API key de aisstream.io"},
    "f_radius": {"en": "Area radius (NM)", "es": "Radio de la zona (millas)"},
    "f_out_host": {"en": "Send AIS to (IP)", "es": "Enviar AIS a (IP)"},
    "f_out_port": {"en": "AIS port → OpenCPN (UDP)", "es": "Puerto AIS → OpenCPN (UDP)"},
    "f_exclude": {"en": "MMSI to ignore (own vessel)", "es": "MMSI a ignorar (el propio)"},
    "f_max_age": {"en": "Discard reports older than (s, 0 = off)", "es": "Descartar datos con más de (s, 0 = no)"},
    "f_rf_hold": {"en": "VHF priority: ignore internet for (min)", "es": "Prioridad VHF: ignorar internet durante (min)"},
    "f_net_tag": {"en": "Mark internet vessel names with", "es": "Marcar nombres de barcos de internet con"},
    "c_test": {"en": "Test vessel 'TEST TARGET' 1 NM north", "es": "Barco de prueba 'TEST TARGET' 1 milla al norte"},
    "c_ais_auto": {"en": "Start AIS automatically on launch", "es": "Iniciar AIS automáticamente al abrir"},
    "ais_hint": {
        "en": "For VHF priority, the OpenCPN output to the GPS port must also\n"
              "include VDM sentences (output filter: RMC and VDM).",
        "es": "Para la prioridad VHF, la salida de OpenCPN al puerto GPS debe\n"
              "incluir también las sentencias VDM (filtro de salida: RMC y VDM).",
    },
    # APRS tab
    "f_callsign": {"en": "Callsign with SSID", "es": "Indicativo con SSID"},
    "f_passcode": {"en": "APRS-IS passcode", "es": "Passcode APRS-IS"},
    "f_server": {"en": "APRS-IS server", "es": "Servidor APRS-IS"},
    "f_server_port": {"en": "Server port", "es": "Puerto servidor"},
    "f_symbol": {"en": "Symbol (/Y sailboat, /s ship)", "es": "Símbolo (/Y velero, /s barco)"},
    "f_comment": {"en": "Comment", "es": "Comentario"},
    "f_int_moving": {"en": "Beacon when moving every (min)", "es": "Baliza navegando cada (min)"},
    "f_int_stopped": {"en": "Beacon when stopped every (min)", "es": "Baliza parado cada (min)"},
    "f_turn": {"en": "Extra beacon on course change (°)", "es": "Baliza extra si cambia el rumbo (°)"},
    "c_aprs_auto": {"en": "Start APRS automatically on launch", "es": "Iniciar APRS automáticamente al abrir"},
    "aprs_hint": {
        "en": "Transmitting to APRS-IS requires an amateur radio licence.",
        "es": "Transmitir a APRS-IS requiere licencia de radioaficionado.",
    },
    # General tab
    "f_gps_port": {"en": "Port for OpenCPN data (UDP, RMC/VDM)", "es": "Puerto datos de OpenCPN (UDP, RMC/VDM)"},
    "f_lat": {"en": "Initial latitude (no GPS)", "es": "Latitud inicial (sin GPS)"},
    "f_lon": {"en": "Initial longitude (no GPS)", "es": "Longitud inicial (sin GPS)"},
    "f_opencpn_path": {"en": "OpenCPN path", "es": "Ruta de OpenCPN"},
    "f_language": {"en": "Language", "es": "Idioma"},
    "c_open_opencpn": {"en": "Open OpenCPN when this app starts", "es": "Abrir OpenCPN al abrir esta app"},
    "general_hint": {
        "en": "The same port is used by AIS and APRS.\n"
              "In OpenCPN: UDP network output to 127.0.0.1 on this port, filter RMC (+ VDM).",
        "es": "El mismo puerto sirve para el AIS y el APRS.\n"
              "En OpenCPN: salida de red UDP a 127.0.0.1 a este puerto, filtro RMC (+ VDM).",
    },
    "lang_auto": {"en": "Automatic", "es": "Automático"},
    # dialogs and log
    "settings_saved": {"en": "Settings saved", "es": "Configuración guardada"},
    "err_title": {"en": "Error", "es": "Error"},
    "err_numbers": {
        "en": "Check the numeric fields (position, radius, ports, intervals, ages).",
        "es": "Revisa los campos numéricos (posición, radio, puertos, intervalos, tiempos).",
    },
    "err_aprs_min": {
        "en": "APRS: minimum 1 min when moving and 5 min when stopped.",
        "es": "APRS: mínimo 1 min navegando y 5 min parado.",
    },
    "missing_key_title": {"en": "API key missing", "es": "Falta la API key"},
    "missing_key": {"en": "Enter it in the AIS tab.", "es": "Introdúcela en la pestaña AIS."},
    "missing_pass_title": {"en": "Passcode missing", "es": "Falta el passcode"},
    "missing_pass": {"en": "Fill it in on the APRS tab.", "es": "Rellénalo en la pestaña APRS."},
    "select_opencpn": {"en": "Select opencpn.exe", "es": "Selecciona opencpn.exe"},
    "program": {"en": "Program", "es": "Programa"},
    "all_files": {"en": "All files", "es": "Todos"},
    "opening_opencpn": {"en": "Opening OpenCPN", "es": "Abriendo OpenCPN"},
    "opencpn_error": {
        "en": "Could not open OpenCPN ({err}). Check the path in General.",
        "es": "No se pudo abrir OpenCPN ({err}). Revisa la ruta en General.",
    },
    "quit_title": {"en": "Quit", "es": "Salir"},
    "quit_question": {
        "en": "Services are running. Stop them and quit?",
        "es": "Hay servicios funcionando. ¿Parar y salir?",
    },

    # ------------------------------------------------------------------- CLI
    "cli_desc": {
        "en": "aisstream.io -> OpenCPN (NMEA AIVDM over UDP)",
        "es": "aisstream.io -> OpenCPN (NMEA AIVDM por UDP)",
    },
    "cli_quit": {"en": "Stopped.", "es": "Parado."},
}

_lang = "en"


def detect_language():
    """Best guess of the system language among the supported ones."""
    try:
        name = (locale.getlocale()[0] or "").lower()
    except (ValueError, TypeError):
        name = ""
    if name.startswith("es") or name.startswith("spanish"):
        return "es"
    return "en"


def set_language(code):
    """Set the active language: a code from LANGUAGES or 'auto'."""
    global _lang
    _lang = detect_language() if code in (None, "", "auto") else code
    if _lang not in LANGUAGES:
        _lang = "en"
    return _lang


def get_language():
    return _lang


def t(key, **kw):
    """Translate a key into the active language and format it."""
    entry = STRINGS.get(key)
    if entry is None:
        return key
    text = entry.get(_lang) or entry["en"]
    return text.format(**kw) if kw else text
