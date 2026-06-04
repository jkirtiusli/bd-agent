# -*- coding: utf-8 -*-
"""Heartbeat best-effort del Agente al Core. Nunca rompe la corrida."""
import json
import logging
import datetime as dt
import urllib.request

log = logging.getLogger("agente.salud")


def _url_heartbeat(url_ingest):
    """Deriva la URL del heartbeat desde la URL de /ingest del config."""
    u = url_ingest.rstrip("/")
    if u.endswith("/ingest"):
        base = u[: -len("/ingest")]
    else:
        base = u
    return base + "/v1/agente/heartbeat"


def _construir_body(cfg, ok, registros, mensaje):
    return {
        "granja": cfg.get("granja"),
        "ok": bool(ok),
        "registros": int(registros or 0),
        "mensaje": mensaje,
        "agente_ts": dt.datetime.now().astimezone().isoformat(),
    }


def reportar(cfg, ok, registros, mensaje):
    """POST best-effort a {core}/v1/agente/heartbeat. Si falla, loguea y sigue."""
    destino = cfg.get("destino", {})
    if destino.get("modo") != "http":
        return  # en modo local_json no se reporta
    url = _url_heartbeat(destino["url"])
    body = _construir_body(cfg, ok, registros, mensaje)
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    token = destino.get("token", "")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status not in (200, 201):
                log.warning(f"[heartbeat] HTTP {resp.status} en {url}")
    except Exception as e:
        log.warning(f"[heartbeat] no se pudo reportar a {url}: {e}")
