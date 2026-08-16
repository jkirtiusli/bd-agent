# -*- coding: utf-8 -*-
"""
Agente BD-Copy -> Core (v3).

Cambio de fondo respecto de v2: el Agente ya no "lee y manda". Ahora
lee -> encola en disco -> drena la cola contra el Core. Una corrida fallida
deja de ser un dia perdido: los registros quedan en la cola y se entregan
en la proxima corrida, sin perder nada.
"""
import sys
import time
import sqlite3
import logging
import argparse
from collections import namedtuple

from bd_agent import __version__
from bd_agent import actualizacion as bd_actualizacion
from bd_agent import config as bd_config
from bd_agent import parser as bd_parser
from bd_agent import destino as bd_destino
from bd_agent import diagnostico as bd_diagnostico
from bd_agent import explorador as bd_explorador
from bd_agent import origen as bd_origen
from bd_agent import salud
from bd_agent import spool as bd_spool

# codigo: 0 ok, o el EXIT_* que corresponda, para que el monitoreo distinga
# "no llego al Core" de "no puedo leer los CSV".
Resultado = namedtuple("Resultado", "ok registros mensaje codigo")


def ciclo_trabajo(cfg, log, sp=None):
    """Lee CSV, encola, drena y reporta heartbeat. Devuelve un Resultado."""
    zona = cfg.get("zona_horaria", "UTC")
    try:
        registros, avisos = bd_parser.escanear(cfg["ruta_csv"], cfg["granja"], zona)
    except OSError as e:
        msg = f"no se pudo leer el origen {cfg['ruta_csv']}: {e}"
        log.error(msg)
        salud.reportar(cfg, ok=False, registros=0, mensaje=msg,
                       extra=salud.reunir_extra(cfg, sp))
        return Resultado(False, 0, msg, bd_config.EXIT_ORIGEN)

    if avisos and cfg.get("centinela", True):
        for nave, archs in avisos.items():
            log.info(f"[centinela] {nave}: datos nuevos sin mapear -> {', '.join(archs)}")

    if cfg["destino"]["modo"] != "http":
        return _entrega_local(cfg, registros, log)

    # La cola vive en un SQLite local: se puede quedar bloqueada (los dos timers
    # la abren a la vez) o quedarse sin disco. Eso no puede matar el proceso con
    # un traceback: se reporta como falla y la corrida devuelve su Resultado.
    try:
        if sp is None:
            with bd_spool.Spool(cfg["spool"]["ruta"]) as propio:
                return _entrega_al_core(cfg, registros, propio, log)
        return _entrega_al_core(cfg, registros, sp, log)
    except (sqlite3.Error, OSError) as e:
        return _fallo_de_cola(cfg, log, e)


def _fallo_de_cola(cfg, log, e):
    """La cola no se pudo abrir/escribir. Se avisa al Core y se corta la corrida."""
    msg = f"no se pudo usar la cola {cfg['spool']['ruta']}: {e}"
    log.error(msg)
    try:
        # Sin la cola no hay estado que leer de ella: se manda el resto del extra.
        salud.reportar(cfg, ok=False, registros=0, mensaje=msg,
                       extra=salud.reunir_extra(cfg, None))
    except Exception as e2:  # el heartbeat es best-effort, no puede tapar el error real
        log.warning(f"tampoco se pudo reportar el latido: {e2}")
    return Resultado(False, 0, msg, bd_config.EXIT_ERROR)


def _entrega_local(cfg, registros, log):
    """Modo local_json: sin cola, se escribe el archivo y listo."""
    try:
        resultado = bd_destino.entregar(registros, cfg["destino"])
    except Exception as e:
        msg = f"error al entregar: {e}"
        log.error(msg)
        return Resultado(False, len(registros), msg, bd_config.EXIT_ERROR)
    log.info(resultado)
    return Resultado(True, len(registros), resultado, bd_config.EXIT_OK)


def _entrega_al_core(cfg, registros, sp, log):
    nuevos = sp.encolar(registros)
    if registros:
        log.info(f"{len(registros)} registros leidos en el origen, {nuevos} nuevos a entregar")
    else:
        log.warning("No se encontraron registros. Revisa 'ruta_csv'.")

    # Se drena aunque el origen este caido: lo que ya estaba en la cola tiene
    # que salir igual.
    enviados, quedan, msg, error = bd_destino.drenar(sp, cfg["destino"])
    if not registros and not enviados and not quedan:
        msg = "sin registros nuevos"

    extra = salud.reunir_extra(cfg, sp)

    # Un origen inalcanzable NO es "sin registros nuevos": si el montaje SMB se
    # cayo, escanear() devuelve vacio y sin este chequeo la granja se veria
    # verde para siempre mientras deja de llegar el dato.
    origen_ok = extra.get("origen_alcanzable", True)
    if not origen_ok:
        msg = f"no se puede leer el origen {cfg['ruta_csv']} — {msg}"
    elif not extra.get("galpones_vistos", 1):
        # La carpeta existe pero no tiene ningun galpon: la ruta apunta al
        # lugar equivocado. Sin esto se ve igual que "no hay datos nuevos".
        origen_ok = False
        msg = (f"la carpeta {cfg['ruta_csv']} no tiene ningun galpon "
               f"(se esperan subcarpetas MANBD_Plc*_House*) — {msg}")

    ok = quedan == 0 and origen_ok
    (log.info if ok else log.error)(msg)
    salud.reportar(cfg, ok=ok, registros=enviados, mensaje=msg, extra=extra)

    codigo = bd_config.EXIT_OK
    if quedan:
        # Por TIPO de error, no por el texto del mensaje: el texto es para leer.
        codigo = (bd_config.EXIT_AUTH if isinstance(error, bd_destino.ErrorAuth)
                  else bd_config.EXIT_RED)
    if not origen_ok:
        codigo = bd_config.EXIT_ORIGEN
    return Resultado(ok, enviados, msg, codigo)


def solo_heartbeat(cfg, log):
    """
    Latido sin tocar los CSV. Corre en su propio timer (cada 5 min) para que el
    Core sepa que el gateway esta vivo aunque el ciclo de datos sea mas lento.
    """
    sp = None
    try:
        try:
            if cfg["destino"]["modo"] == "http":
                sp = bd_spool.Spool(cfg["spool"]["ruta"])
            extra = salud.reunir_extra(cfg, sp)
        finally:
            if sp is not None:
                sp.cerrar()
    except (sqlite3.Error, OSError) as e:
        return _fallo_de_cola(cfg, log, e)
    pendientes = extra.get("pendientes_en_cola", 0)
    ok = bool(extra.get("origen_alcanzable")) and not pendientes
    msg = "latido" if ok else f"latido con novedades ({pendientes} en cola)"
    salud.reportar(cfg, ok=ok, registros=0, mensaje=msg, extra=extra)
    log.info(f"{msg} | {extra}")
    return Resultado(ok, 0, msg, bd_config.EXIT_OK)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Agente BD-Copy")
    ap.add_argument("--config", help="ruta al config.yaml de la granja")
    ap.add_argument("--once", action="store_true",
                    help="una sola corrida (lo que usan systemd / la tarea programada)")
    ap.add_argument("--diagnostico", action="store_true",
                    help="revisa config, origen, cola, Core y reloj; no entrega datos")
    ap.add_argument("--configurar", action="store_true",
                    help="asistente para elegir la carpeta de BD-Copy (con explorador)")
    ap.add_argument("--explorar", action="store_true",
                    help="lista que datos hay en los CSV, mapeados y sin mapear")
    ap.add_argument("--solo-heartbeat", action="store_true",
                    help="reporta estado al Core sin leer los CSV")
    ap.add_argument("--reenviar-desde", metavar="AAAA-MM-DD",
                    help="vuelve a encolar todo lo confirmado desde esa fecha")
    ap.add_argument("--actualizar", action="store_true",
                    help="busca version nueva en el canal, la verifica y se reemplaza")
    ap.add_argument("--revisar", action="store_true",
                    help="con --actualizar: solo informa, no instala nada")
    ap.add_argument("--desatendido", action="store_true",
                    help="con --actualizar: lo corre el timer, respeta actualizacion.modo")
    ap.add_argument("--revertir", action="store_true",
                    help="vuelve a la version anterior guardada")
    ap.add_argument("--log-nivel", default="INFO",
                    choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    ap.add_argument("--version", action="version", version=f"agente-bd-copy {__version__}")
    args = ap.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.log_nivel),
                        format="%(asctime)s [%(levelname)s] %(message)s")
    log = logging.getLogger("agente")

    if not args.config:
        ap.error("--config es obligatorio")

    # Va antes de cargar la config: el asistente sirve justamente para cuando
    # todavia no hay una config valida.
    if args.configurar:
        return bd_origen.configurar_interactivo(args.config)

    if args.diagnostico:
        return bd_diagnostico.correr(args.config)

    if args.explorar:
        return bd_explorador.correr(args.config)

    # Tambien antes de cargar la config: revertir es puro sistema de archivos y
    # no mira cfg. Si una actualizacion dejo la config rota, volver atras es
    # justamente lo unico que hay que poder hacer.
    if args.revertir:
        try:
            destino = bd_actualizacion.revertir()
        except bd_actualizacion.ErrorActualizacion as e:
            log.error(f"no se pudo revertir: {e}")
            return bd_config.EXIT_ERROR
        log.info(f"revertido a la version anterior en {destino}")
        return bd_config.EXIT_OK

    try:
        cfg = bd_config.cargar(args.config)
    except bd_config.ErrorConfig as e:
        log.error(f"config invalida: {e}")
        return bd_config.EXIT_CONFIG

    log.info(f"Agente v{__version__} | granja={cfg['granja']} | "
             f"modo={cfg['destino']['modo']} | tz={cfg['zona_horaria']}")

    if args.reenviar_desde:
        with bd_spool.Spool(cfg["spool"]["ruta"]) as sp:
            n = sp.reencolar_desde(args.reenviar_desde)
        log.info(f"{n} registros reencolados desde {args.reenviar_desde}")
        return bd_config.EXIT_OK

    if args.actualizar:
        # Una actualizacion que falla NO es un incidente de datos: se reporta
        # y se sigue. Por eso nunca devuelve codigo de error.
        _cambio, mensaje = bd_actualizacion.actualizar(
            cfg, log, revisar=args.revisar, desatendido=args.desatendido)
        log.info(mensaje)
        return bd_config.EXIT_OK

    if args.solo_heartbeat:
        return solo_heartbeat(cfg, log).codigo

    if args.once:
        return ciclo_trabajo(cfg, log).codigo

    # Modo demonio. Con el gateway se usa systemd (--once + timer), pero se
    # mantiene para instalaciones sin systemd.
    intervalo = cfg.get("intervalo_segundos", 900)
    while True:
        try:
            ciclo_trabajo(cfg, log)
        except Exception as e:
            log.exception(f"Error en ciclo: {e}")
        time.sleep(intervalo)


if __name__ == "__main__":
    sys.exit(main())
