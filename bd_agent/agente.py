# -*- coding: utf-8 -*-
"""Agente BD-Copy -> Core (v2)."""
import time
import logging
import argparse
import yaml

from bd_agent import parser as bd_parser
from bd_agent import destino as bd_destino

def cargar_config(ruta):
    with open(ruta, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def ciclo_trabajo(cfg, log):
    zona = cfg.get("zona_horaria", "UTC")
    registros, avisos = bd_parser.escanear(cfg["ruta_csv"], cfg["granja"], zona)
    if not registros:
        log.warning("No se encontraron registros. Revisa 'ruta_csv'.")
        return
    # Centinela (apagado: solo informa en log, no notifica)
    if avisos and cfg.get("centinela", True):
        for nave, archs in avisos.items():
            log.info(f"[centinela] {nave}: datos nuevos sin mapear -> {', '.join(archs)}")
    resultado = bd_destino.entregar(registros, cfg["destino"])
    log.info(resultado)

def main():
    ap = argparse.ArgumentParser(description="Agente BD-Copy")
    ap.add_argument("--config", required=True)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    log = logging.getLogger("agente")

    cfg = cargar_config(args.config)
    log.info(f"Agente v2 | granja={cfg['granja']} | modo={cfg['destino']['modo']} | tz={cfg.get('zona_horaria')}")

    if args.once:
        ciclo_trabajo(cfg, log)
        return

    intervalo = cfg.get("intervalo_segundos", 300)
    while True:
        try:
            ciclo_trabajo(cfg, log)
        except Exception as e:
            log.error(f"Error en ciclo: {e}")
        time.sleep(intervalo)

if __name__ == "__main__":
    main()
