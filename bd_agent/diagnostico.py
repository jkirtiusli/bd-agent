# -*- coding: utf-8 -*-
"""
`--diagnostico`: lo primero que se corre cuando una granja falla.

Chequea en orden lo que se puede romper, y dice cual es el problema en
castellano. Nunca imprime el token.
"""
import os
import time
import socket
import datetime as dt
import urllib.error
import urllib.request

from bd_agent import __version__, config as bd_config
from bd_agent import salud, spool as bd_spool
from bd_agent.parser import parse_nombre_nave

OK, AVISO, MAL = "OK  ", "AVISO", "MAL "


def _linea(estado, titulo, detalle=""):
    return f"[{estado}] {titulo}" + (f" — {detalle}" if detalle else "")


def correr(ruta_config, imprimir=print):
    """Devuelve el codigo de salida (0 = todo bien)."""
    imprimir(f"Agente BD-Copy v{__version__} | {socket.gethostname()} | "
             f"{dt.datetime.now().astimezone().isoformat(timespec='seconds')}")
    imprimir("-" * 72)

    # 1. Config
    try:
        cfg = bd_config.cargar(ruta_config)
    except bd_config.ErrorConfig as e:
        imprimir(_linea(MAL, "Config", str(e)))
        return bd_config.EXIT_CONFIG
    destino = cfg["destino"]
    imprimir(_linea(OK, "Config", f"granja={cfg['granja']} modo={destino['modo']} "
                                  f"tz={cfg['zona_horaria']}"))
    if destino["modo"] == "http":
        imprimir(_linea(OK, "Token", f"presente ({len(destino['token'])} chars) "
                                     f"desde {bd_config.origen_token(destino)}"))

    peor = bd_config.EXIT_OK

    # 2. Origen de los CSV
    ruta_csv = cfg["ruta_csv"]
    est = salud.estado_origen(ruta_csv)
    if not est["origen_alcanzable"]:
        imprimir(_linea(MAL, "Origen", f"no se puede leer {ruta_csv} "
                                       f"(¿el montaje SMB esta caido?)"))
        peor = bd_config.EXIT_ORIGEN
    else:
        naves = [c for c in sorted(os.listdir(ruta_csv))
                 if os.path.isdir(os.path.join(ruta_csv, c))
                 and parse_nombre_nave(c)[0]]
        no_reconocidas = est["galpones_vistos"] - len(naves)
        imprimir(_linea(OK, "Origen", f"{ruta_csv} — {len(naves)} naves reconocidas"))
        if no_reconocidas > 0:
            imprimir(_linea(AVISO, "Naves", f"{no_reconocidas} carpetas no matchean "
                                            f"MANBD_Plc*_House*_<ciclo>"))
        frescura = est["fuente_frescura_seg"]
        if frescura is None:
            imprimir(_linea(MAL, "Frescura", "no hay ningun archivo en las carpetas"))
            peor = peor or bd_config.EXIT_ORIGEN
        elif frescura > 6 * 3600:
            imprimir(_linea(AVISO, "Frescura", f"el CSV mas nuevo tiene {frescura // 3600} h "
                                               f"— BD-Copy podria no estar exportando"))
        else:
            imprimir(_linea(OK, "Frescura", f"CSV mas nuevo hace {frescura // 60} min"))

    # 3. Cola local
    ruta_spool = cfg["spool"]["ruta"]
    try:
        with bd_spool.Spool(ruta_spool) as sp:
            e = sp.estado()
        libre = salud.disco_libre_mb(ruta_spool)
        imprimir(_linea(OK, "Cola", f"{ruta_spool} — {e['pendientes_en_cola']} pendientes, "
                                    f"{e['confirmados_total']} confirmados, "
                                    f"ultimo dato {e['ultimo_dato_fecha'] or 'ninguno'}"))
        if e["ultimo_error"]:
            imprimir(_linea(AVISO, "Ultimo error de entrega", e["ultimo_error"]))
        if libre is not None and libre < 500:
            imprimir(_linea(AVISO, "Disco", f"quedan {libre} MB libres"))
    except Exception as ex:
        imprimir(_linea(MAL, "Cola", f"no se pudo abrir {ruta_spool}: {ex}"))
        peor = peor or bd_config.EXIT_ERROR

    # 4. Core
    if destino["modo"] == "http":
        codigo = _probar_core(cfg, imprimir)
        peor = peor or codigo
    else:
        imprimir(_linea(AVISO, "Core", "modo local_json: no se entrega al Core"))

    imprimir("-" * 72)
    imprimir("Diagnostico OK" if peor == bd_config.EXIT_OK
             else f"Diagnostico con problemas (codigo {peor})")
    return peor


def _probar_core(cfg, imprimir):
    """Latido de prueba: valida DNS, TLS, token y reloj de una sola vez."""
    destino = cfg["destino"]
    url = salud._url_heartbeat(destino["url"])
    body = salud._construir_body(cfg, ok=True, registros=0,
                                 mensaje="diagnostico", extra={"prueba": True})
    import json as _json
    req = urllib.request.Request(
        url, data=_json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {destino['token']}")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            ms = int((time.monotonic() - t0) * 1000)
            imprimir(_linea(OK, "Core", f"{url} responde {resp.status} en {ms} ms"))
            _comparar_reloj(resp.headers.get("Date"), imprimir)
        return bd_config.EXIT_OK
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            imprimir(_linea(MAL, "Core", f"token rechazado (HTTP {e.code}) en {url}"))
            return bd_config.EXIT_AUTH
        imprimir(_linea(MAL, "Core", f"HTTP {e.code} en {url}"))
        return bd_config.EXIT_RED
    except urllib.error.URLError as e:
        imprimir(_linea(MAL, "Core", f"no se llega a {url}: {e.reason}"))
        return bd_config.EXIT_RED
    except OSError as e:
        imprimir(_linea(MAL, "Core", f"error de red hacia {url}: {e}"))
        return bd_config.EXIT_RED


def _comparar_reloj(cabecera_date, imprimir):
    """
    Todo el modelo de datos es por fecha: un reloj corrido rompe el corte
    diario en silencio. Se compara contra la hora del Core.
    """
    if not cabecera_date:
        return
    try:
        from email.utils import parsedate_to_datetime
        remoto = parsedate_to_datetime(cabecera_date)
        desfase = abs((dt.datetime.now(dt.timezone.utc) - remoto).total_seconds())
    except (TypeError, ValueError):
        return
    if desfase > 120:
        imprimir(_linea(AVISO, "Reloj", f"desfasado {int(desfase)} s respecto del Core "
                                        f"— revisar NTP"))
    else:
        imprimir(_linea(OK, "Reloj", f"en hora (desfase {int(desfase)} s)"))
