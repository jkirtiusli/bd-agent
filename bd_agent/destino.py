# -*- coding: utf-8 -*-
"""Entrega de datos al destino. LOCAL (json) o NUBE/Core (http)."""
import json
import os

def entregar(registros, cfg_destino):
    modo = cfg_destino.get("modo", "local_json")
    if modo == "local_json":
        return _a_json_local(registros, cfg_destino["ruta_salida"])
    elif modo == "http":
        return _a_http(registros, cfg_destino["url"], cfg_destino.get("token", ""),
                       int(cfg_destino.get("lote", 2000)))
    else:
        raise ValueError(f"Modo de destino desconocido: {modo}")

def _a_json_local(registros, ruta_salida):
    os.makedirs(os.path.dirname(ruta_salida), exist_ok=True)
    tmp = ruta_salida + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(registros, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ruta_salida)
    return f"{len(registros)} registros escritos en {ruta_salida}"

def _a_http(registros, url, token, lote):
    """Empuja en lotes (un solo POST con 141k registros seria enorme)."""
    import urllib.request
    enviados = 0
    for i in range(0, len(registros), lote):
        bloque = registros[i:i+lote]
        body = json.dumps(bloque, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=60) as resp:
            if resp.status not in (200, 201):
                raise RuntimeError(f"HTTP {resp.status} en lote {i}")
        enviados += len(bloque)
    return f"{enviados} registros enviados a {url} en lotes de {lote}"
