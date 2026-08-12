# -*- coding: utf-8 -*-
"""
Heartbeat best-effort del Agente al Core. Nunca rompe la corrida.

El latido no es solo "estoy vivo": lleva el estado suficiente para que del otro
lado se pueda diagnosticar sin entrar a la granja. El campo mas util es
`fuente_frescura_seg` (antiguedad del CSV mas nuevo), porque distingue dos
fallas que hoy se ven igual (silencio) y se arreglan distinto:

    gateway caido            -> no llega ningun latido
    BD-Copy no exporta       -> llega el latido, con frescura alta

La deteccion de "esta granja se murio" NO puede vivir aca: si el agente no
corre, no hay latido. Esa deteccion es por AUSENCIA y vive en el Core
(dead-man's switch).
"""
import os
import json
import socket
import shutil
import logging
import datetime as dt
import urllib.request

from bd_agent import __version__
from bd_agent.parser import parse_nombre_nave

log = logging.getLogger("agente.salud")


def _url_heartbeat(url_ingest):
    """Deriva la URL del heartbeat desde la URL de /ingest del config."""
    u = url_ingest.rstrip("/")
    if u.endswith("/ingest"):
        base = u[: -len("/ingest")]
    else:
        base = u
    return base + "/v1/agente/heartbeat"


def _construir_body(cfg, ok, registros, mensaje, extra=None):
    body = {
        "granja": cfg.get("granja"),
        "ok": bool(ok),
        "registros": int(registros or 0),
        "mensaje": mensaje,
        "agente_ts": dt.datetime.now().astimezone().isoformat(),
        "version_agente": __version__,
        "hostname": socket.gethostname(),
        "zona_horaria": cfg.get("zona_horaria"),
    }
    if extra:
        body.update({k: v for k, v in extra.items() if v is not None})
    return body


def estado_origen(ruta_csv):
    """
    Frescura y alcance de la carpeta de CSV.

    frescura_seg = antiguedad del archivo modificado mas recientemente. Si sube
    de unas horas, BD-Copy dejo de exportar aunque el gateway este perfecto.

    galpones_vistos cuenta solo las carpetas RECONOCIDAS como galpon. Cero
    galpones con la carpeta existiendo = la ruta apunta al lugar equivocado,
    que es distinto de "no hay datos nuevos" y merece alerta propia.
    """
    if not ruta_csv or not os.path.isdir(ruta_csv):
        return {"origen_alcanzable": False, "fuente_frescura_seg": None,
                "galpones_vistos": 0, "carpetas_totales": 0}
    mas_nuevo, galpones, carpetas = None, 0, 0
    try:
        for carpeta in os.scandir(ruta_csv):
            if not carpeta.is_dir():
                continue
            carpetas += 1
            if not parse_nombre_nave(carpeta.name)[0]:
                continue
            galpones += 1
            for arch in os.scandir(carpeta.path):
                if not arch.is_file():
                    continue
                mtime = arch.stat().st_mtime
                if mas_nuevo is None or mtime > mas_nuevo:
                    mas_nuevo = mtime
    except OSError as e:
        log.warning(f"[salud] no se pudo recorrer {ruta_csv}: {e}")
        return {"origen_alcanzable": False, "fuente_frescura_seg": None,
                "galpones_vistos": galpones, "carpetas_totales": carpetas}
    frescura = None if mas_nuevo is None else int(max(0, dt.datetime.now().timestamp() - mas_nuevo))
    return {"origen_alcanzable": True, "fuente_frescura_seg": frescura,
            "galpones_vistos": galpones, "carpetas_totales": carpetas}


def disco_libre_mb(ruta):
    """Un gateway con el disco lleno deja de encolar en silencio. Que se vea."""
    try:
        objetivo = ruta if os.path.isdir(ruta) else os.path.dirname(os.path.abspath(ruta))
        return int(shutil.disk_usage(objetivo or ".").free / (1024 * 1024))
    except OSError:
        return None


def reunir_extra(cfg, spool=None):
    """Junta todo el estado que acompaña al latido."""
    extra = estado_origen(cfg.get("ruta_csv"))
    extra["disco_libre_mb"] = disco_libre_mb(cfg.get("spool", {}).get("ruta", "."))
    if spool is not None:
        try:
            extra.update(spool.estado())
        except Exception as e:  # la cola no puede tumbar el latido
            log.warning(f"[salud] no se pudo leer el estado de la cola: {e}")
    try:
        # Resultado de la ultima actualizacion: asi se ve desde el tablero si
        # una granja quedo trabada intentando actualizarse.
        from bd_agent import actualizacion
        extra["ultima_actualizacion"] = actualizacion.leer_estado(cfg)
    except Exception as e:
        log.warning(f"[salud] no se pudo leer el estado de actualizacion: {e}")
    return extra


def reportar(cfg, ok, registros, mensaje, extra=None):
    """POST best-effort a {core}/v1/agente/heartbeat. Si falla, loguea y sigue."""
    destino = cfg.get("destino", {})
    if destino.get("modo") != "http":
        return  # en modo local_json no se reporta
    url = _url_heartbeat(destino["url"])
    body = _construir_body(cfg, ok, registros, mensaje, extra)
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    token = destino.get("token", "")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status not in (200, 201, 202, 204):
                log.warning(f"[heartbeat] HTTP {resp.status} en {url}")
    except Exception as e:
        log.warning(f"[heartbeat] no se pudo reportar a {url}: {e}")
