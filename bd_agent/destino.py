# -*- coding: utf-8 -*-
"""
Entrega de datos al destino. Aqui vive la decision LOCAL vs NUBE.
El resto del agente no sabe ni le importa a donde van los datos.
"""
import json
import os

def entregar(registros, cfg_destino):
    modo = cfg_destino.get("modo", "local_json")
    if modo == "local_json":
        return _a_json_local(registros, cfg_destino["ruta_salida"])
    elif modo == "http":
        return _a_http(registros, cfg_destino["url"], cfg_destino.get("token", ""))
    else:
        raise ValueError(f"Modo de destino desconocido: {modo}")

def _a_json_local(registros, ruta_salida):
    os.makedirs(os.path.dirname(ruta_salida), exist_ok=True)
    # Escritura atomica: escribe a tmp y renombra, asi quien lea nunca ve un archivo a medias
    tmp = ruta_salida + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(registros, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ruta_salida)
    return f"{len(registros)} registros escritos en {ruta_salida}"

def _a_http(registros, url, token):
    # Import local para no exigir 'requests' si solo se usa modo local
    import urllib.request
    body = json.dumps(registros, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return f"{len(registros)} registros enviados a {url} (HTTP {resp.status})"
