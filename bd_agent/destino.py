# -*- coding: utf-8 -*-
"""
Entrega de datos al destino: LOCAL (json) o CORE (http).

Dos cosas que antes no estaban y son las que sostienen la corrida desatendida:

1. REINTENTOS CON BACKOFF. Un corte de red de unos minutos ya no pierde la
   corrida. Se distingue lo reintentable (timeout, 5xx, 429) de lo permanente
   (400, 401, 403): a lo permanente no tiene sentido insistirle, hay que avisar.

2. CONFIRMACION POR LOTE. Cada lote que el Core acusa OK se confirma en la cola
   por separado. Antes, si fallaba el lote 40, los 39 anteriores ya habian
   entrado pero la corrida entera se reportaba como error y no quedaba registro
   de que si habia pasado.
"""
import os
import json
import time
import random
import hashlib
import logging
import urllib.error
import urllib.request
from collections import namedtuple

log = logging.getLogger("agente.destino")

# `error` lleva la excepcion que corto el drenaje (o None). El que llama decide
# el codigo de salida por TIPO (ErrorAuth, ErrorReintentable...), no leyendo el
# mensaje: el texto es para humanos y puede cambiar sin avisar.
Drenaje = namedtuple("Drenaje", "enviados quedan mensaje error")

# 429 y 5xx: el Core esta saturado o caido. 408/425: timeout del lado servidor.
HTTP_REINTENTABLES = {408, 425, 429, 500, 502, 503, 504, 507, 509}
HTTP_AUTH = {401, 403}

# Freno de mano: con lotes de 2000 son 20M de registros. Si se llega aca,
# algo esta mal y es mejor cortar que girar en falso.
MAX_LOTES = 10000


class ErrorReintentable(Exception):
    """Falla transitoria: red, timeout, 5xx. Se reintenta."""


class ErrorPermanente(Exception):
    """Falla que no se arregla insistiendo: 400, 422. Hay que avisar."""


class ErrorAuth(ErrorPermanente):
    """Token invalido o sin permiso. Se distingue para el codigo de salida."""


# --------------------------------------------------------------------------
# Entrega directa, sin cola (local_json, o http de un tiron)
# --------------------------------------------------------------------------

def entregar(registros, cfg_destino):
    modo = cfg_destino.get("modo", "local_json")
    if modo == "local_json":
        return _a_json_local(registros, cfg_destino["ruta_salida"])
    if modo == "http":
        enviados = 0
        lote = int(cfg_destino.get("lote", 2000))
        for i in range(0, len(registros), lote):
            bloque = registros[i:i + lote]
            enviar_lote(bloque, cfg_destino)
            enviados += len(bloque)
        return f"{enviados} registros enviados a {cfg_destino['url']} en lotes de {lote}"
    raise ValueError(f"Modo de destino desconocido: {modo}")


def _a_json_local(registros, ruta_salida):
    carpeta = os.path.dirname(os.path.abspath(ruta_salida))
    if carpeta:
        os.makedirs(carpeta, exist_ok=True)
    tmp = ruta_salida + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(registros, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ruta_salida)  # atomico: nunca se lee un json a medio escribir
    return f"{len(registros)} registros escritos en {ruta_salida}"


# --------------------------------------------------------------------------
# Envio de un lote, con reintentos
# --------------------------------------------------------------------------

def clave_idempotencia(bloque):
    """
    Deterministica: un reintento despues de un timeout ambiguo manda la misma
    clave, asi el Core descarta el duplicado en vez de contarlo dos veces.
    """
    crudo = "|".join(sorted(str(r.get("clave", "")) for r in bloque))
    if not crudo.strip("|"):  # los registros no traen clave (entrega sin cola)
        crudo = json.dumps(bloque, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(crudo.encode("utf-8")).hexdigest()


def _post(bloque, cfg_destino):
    """Un POST. Traduce la falla a ErrorReintentable / ErrorPermanente."""
    url = cfg_destino["url"]
    body = json.dumps(bloque, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Idempotency-Key", clave_idempotencia(bloque))
    token = cfg_destino.get("token", "")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    timeout = int(cfg_destino.get("timeout", 60))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status not in (200, 201, 202, 204):
                raise ErrorReintentable(f"HTTP {resp.status} en {url}")
            return resp.status
    except urllib.error.HTTPError as e:
        detalle = _detalle(e)
        if e.code in HTTP_AUTH:
            raise ErrorAuth(f"HTTP {e.code} (token rechazado) en {url}: {detalle}")
        if e.code in HTTP_REINTENTABLES:
            err = ErrorReintentable(f"HTTP {e.code} en {url}: {detalle}")
            err.retry_after = _cabecera_retry_after(e)
            raise err
        raise ErrorPermanente(f"HTTP {e.code} en {url}: {detalle}")
    except urllib.error.URLError as e:
        raise ErrorReintentable(f"sin conexion a {url}: {e.reason}")
    except (TimeoutError, OSError) as e:
        raise ErrorReintentable(f"error de red hacia {url}: {e}")


def _detalle(http_error):
    try:
        return http_error.read().decode("utf-8", "replace")[:300]
    except Exception:
        return str(http_error.reason)


def _cabecera_retry_after(http_error):
    """Si el servidor dice cuanto esperar, le hacemos caso."""
    try:
        crudo = http_error.headers.get("Retry-After")
        return float(crudo) if crudo is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def enviar_lote(bloque, cfg_destino, dormir=time.sleep):
    """
    Manda un lote reintentando con backoff exponencial + jitter.
    Propaga ErrorReintentable si se agotaron los intentos, o ErrorPermanente.
    """
    intentos = max(int(cfg_destino.get("reintentos", 5)), 1)
    base = float(cfg_destino.get("backoff_base", 1.0))
    tope = float(cfg_destino.get("backoff_tope", 60.0))
    ultimo = None
    for intento in range(1, intentos + 1):
        try:
            return _post(bloque, cfg_destino)
        except ErrorPermanente:
            raise  # insistir no lo arregla
        except ErrorReintentable as e:
            ultimo = e
            if intento >= intentos:
                break
            espera = getattr(e, "retry_after", None)
            if espera is None:
                espera = min(base * (2 ** (intento - 1)), tope)
                espera += random.uniform(0, espera * 0.25)  # jitter: no sincronizar granjas
            log.warning(f"[destino] intento {intento}/{intentos} fallo ({e}); "
                        f"reintento en {espera:.1f}s")
            dormir(espera)
    raise ErrorReintentable(f"agotados {intentos} intentos: {ultimo}")


# --------------------------------------------------------------------------
# Drenaje de la cola
# --------------------------------------------------------------------------

def drenar(spool, cfg_destino, dormir=time.sleep):
    """
    Vacia la cola contra el Core, lote por lote, confirmando lo que entra.

    Devuelve un Drenaje(enviados, quedan_pendientes, mensaje, error), donde
    `error` es la excepcion que corto el drenaje (ErrorAuth, ErrorReintentable)
    o None si termino limpio.

    - Error permanente en un lote: se anota el motivo y se sigue con el
      siguiente (el lote rechazado queda en la cola, y se saltea con `offset`
      para no volver a tomarlo en esta misma pasada). Un dato malo no puede
      bloquear a todos los que vienen atras.
    - Error reintentable con los intentos agotados: se corta el drenaje. La red
      esta caida, no tiene sentido martillarla; la proxima corrida sigue desde
      donde quedo.
    """
    lote = int(cfg_destino.get("lote", 2000))
    enviados, rechazados, lotes = 0, 0, 0
    corte = None
    while lotes < MAX_LOTES:
        bloque = spool.tomar(lote, offset=rechazados)
        if not bloque:
            break
        lotes += 1
        claves = [k for k, _vh, _p in bloque]
        payloads = [p for _k, _vh, p in bloque]
        try:
            enviar_lote(payloads, cfg_destino, dormir=dormir)
        except ErrorAuth as e:
            # El token no sirve: todos los lotes van a fallar igual.
            log.error(f"[destino] token rechazado: {e}")
            spool.registrar_error(claves, e)
            corte = e
            break
        except ErrorPermanente as e:
            log.error(f"[destino] lote rechazado definitivamente: {e}")
            spool.registrar_error(claves, e)
            rechazados += len(claves)
            continue
        except ErrorReintentable as e:
            log.error(f"[destino] no se pudo entregar el lote: {e}")
            spool.registrar_error(claves, e)
            corte = e
            break
        spool.confirmar(claves)
        enviados += len(claves)

    quedan = spool.estado()["pendientes_en_cola"]
    partes = [f"{enviados} enviados"]
    if rechazados:
        partes.append(f"{rechazados} rechazados")
    if quedan:
        partes.append(f"{quedan} en cola")
    mensaje = ", ".join(partes)
    if corte is not None:
        mensaje += f" — corte: {corte}"
    return Drenaje(enviados, quedan, mensaje, corte)
