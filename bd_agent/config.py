# -*- coding: utf-8 -*-
"""
Carga y validacion de la config de la granja.

El token NO tiene por que estar en el YAML. Se resuelve en este orden:
    1. destino.token_file  -> ruta a un archivo (permisos 0600) con el token
    2. destino.token_env   -> nombre de una variable de entorno
    3. destino.token       -> el token en linea (comodo para probar, no para produccion)

En `token_file` se expanden variables de entorno, asi funciona con
LoadCredential= de systemd:  token_file: "${CREDENTIALS_DIRECTORY}/core_token"
"""
import os
import yaml

# Codigos de salida: cada clase de error se distingue desde el monitoreo.
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_CONFIG = 2
EXIT_ORIGEN = 3
EXIT_RED = 4
EXIT_AUTH = 5

REQUERIDOS = ("granja", "ruta_csv", "destino")


class ErrorConfig(Exception):
    """Config ausente, ilegible o incompleta."""


def cargar(ruta):
    """Lee el YAML, aplica defaults y resuelve el token. Devuelve el dict."""
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except FileNotFoundError:
        raise ErrorConfig(f"no existe el archivo de config: {ruta}")
    except yaml.YAMLError as e:
        raise ErrorConfig(f"YAML invalido en {ruta}: {e}")
    if not isinstance(cfg, dict):
        raise ErrorConfig(f"la config de {ruta} no es un mapa de claves")

    faltan = [c for c in REQUERIDOS if not cfg.get(c)]
    if faltan:
        raise ErrorConfig(f"faltan claves en {ruta}: {', '.join(faltan)}")

    cfg.setdefault("zona_horaria", "UTC")
    cfg.setdefault("intervalo_segundos", 900)
    cfg.setdefault("centinela", True)
    cfg["_ruta_config"] = os.path.abspath(ruta)

    destino = cfg["destino"]
    if not isinstance(destino, dict):
        raise ErrorConfig("'destino' tiene que ser un mapa de claves")
    destino.setdefault("modo", "local_json")
    destino.setdefault("lote", 2000)
    destino.setdefault("reintentos", 5)
    destino.setdefault("backoff_base", 1.0)
    destino.setdefault("backoff_tope", 60.0)
    destino.setdefault("timeout", 60)
    if destino["modo"] == "http":
        if not destino.get("url"):
            raise ErrorConfig("destino.modo es 'http' pero falta destino.url")
        destino["token"] = resolver_token(destino)
    elif destino["modo"] == "local_json":
        if not destino.get("ruta_salida"):
            raise ErrorConfig("destino.modo es 'local_json' pero falta destino.ruta_salida")
    else:
        raise ErrorConfig(f"destino.modo desconocido: {destino['modo']}")

    cfg["spool"] = cfg.get("spool") or {}
    cfg["spool"].setdefault("ruta", ruta_spool_por_defecto(cfg))

    # Auto-actualizacion. Por defecto MANUAL: el timer consulta pero no aplica
    # hasta que se pase a "automatica". Se empieza por una granja (canario) y
    # recien despues se abre a la flota.
    cfg["actualizacion"] = cfg.get("actualizacion") or {}
    cfg["actualizacion"].setdefault("modo", "manual")
    cfg["actualizacion"].setdefault("max_mb", 60)
    modo = cfg["actualizacion"]["modo"]
    if modo not in ("manual", "automatica"):
        raise ErrorConfig(f"actualizacion.modo desconocido: {modo} "
                          f"(esperado manual | automatica)")
    return cfg


def ruta_spool_por_defecto(cfg):
    """La cola vive al lado de la config, salvo que se indique otra cosa."""
    base = os.path.dirname(cfg.get("_ruta_config") or ".") or "."
    return os.path.join(base, "spool.db")


def resolver_token(destino):
    """Devuelve el token segun la fuente configurada. Nunca lo loguea."""
    ruta = destino.get("token_file")
    if ruta:
        ruta = os.path.expandvars(os.path.expanduser(ruta))
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                token = f.read().strip()
        except OSError as e:
            raise ErrorConfig(f"no se pudo leer destino.token_file ({ruta}): {e}")
        if not token:
            raise ErrorConfig(f"destino.token_file esta vacio: {ruta}")
        return token

    nombre = destino.get("token_env")
    if nombre:
        token = os.environ.get(nombre, "").strip()
        if not token:
            raise ErrorConfig(f"la variable de entorno {nombre} esta vacia o no existe")
        return token

    token = (destino.get("token") or "").strip()
    if not token or token == "PEGAR_TOKEN":
        raise ErrorConfig(
            "falta el token de ingesta: configura destino.token_file, "
            "destino.token_env o destino.token"
        )
    return token


def origen_token(destino):
    """De donde sale el token, para mostrarlo en el diagnostico (sin el valor)."""
    if destino.get("token_file"):
        return f"archivo ({os.path.expandvars(destino['token_file'])})"
    if destino.get("token_env"):
        return f"variable de entorno ({destino['token_env']})"
    return "config.yaml (en linea)"
