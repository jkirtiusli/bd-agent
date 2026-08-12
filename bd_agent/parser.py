# -*- coding: utf-8 -*-
"""
Parser + normalizador BD-Copy -> modelo canonico (v2).
- Lista canonica de metricas (metricas.py) con respaldo de fuentes.
- Campos temporales explicitos: fecha_dato, hora_cierre, zona_horaria, capturado_en.
- Descarta el dia en curso (solo dias cerrados).
- Centinela: detecta archivos con datos fuera de la lista canonica (los reporta, no los procesa).
"""
import os
import re
import logging
import datetime as dt
import pandas as pd

from bd_agent.metricas import CANONICAS, ARCHIVOS_CONOCIDOS

log = logging.getLogger("agente.parser")

_RE_NAVE = re.compile(r"MANBD_(Plc\d+_House\w+?)_(\d+)$", re.IGNORECASE)
_RE_FECHA = re.compile(r"\d{2}\.\d{2}\.\d{4}")

def parse_nombre_nave(carpeta):
    m = _RE_NAVE.search(carpeta)
    if not m:
        return None, None
    return m.group(1), m.group(2)

def _hora_cierre(time_str):
    """De '26/5/2026 22:00' saca '22:00'."""
    m = re.search(r"(\d{1,2}:\d{2})", str(time_str))
    return m.group(1) if m else None

def _leer_df(ruta):
    """
    Un archivo ilegible no puede tumbar la corrida entera.

    Pasa de verdad: BD-Copy escribiendo el CSV justo cuando el agente lo lee,
    un archivo truncado por un corte de luz, o encoding roto. Antes, cualquiera
    de esos casos propagaba la excepcion y se perdian TODOS los galpones.
    """
    try:
        df = pd.read_csv(ruta, sep="\t", encoding="latin-1",
                         engine="python", on_bad_lines="skip")
    except Exception as e:  # pandas tira EmptyDataError, ParserError, UnicodeError...
        log.warning(f"[parser] no se pudo leer {os.path.basename(ruta)}: {e}")
        return None
    if "DATE" not in df.columns:
        return None
    df = df[df["DATE"].astype(str).str.match(_RE_FECHA)].copy()
    if df.empty:
        return None
    df["FECHA"] = pd.to_datetime(df["DATE"], format="%d.%m.%Y", errors="coerce")
    df = df.dropna(subset=["FECHA"]).sort_values("FECHA")
    return df

def _tiene_datos(df):
    if df is None or "NUM" not in df.columns:
        return False
    num = pd.to_numeric(df["NUM"], errors="coerce").fillna(0)
    return int((num != 0).sum()) > 0

def _elegir_fuente(ruta_nave, fuentes):
    """Devuelve (df, archivo) de la primera fuente con datos; o (None, None)."""
    for arch in fuentes:
        ruta = os.path.join(ruta_nave, arch)
        if not os.path.exists(ruta):
            continue
        df = _leer_df(ruta)
        if _tiene_datos(df):
            return df, arch
    return None, None

def procesar_nave(ruta_nave, granja, zona_horaria, hoy=None):
    """
    Devuelve (registros, aviso_centinela) para una nave.
    Descarta el dia en curso (fecha == hoy).
    """
    hoy = hoy or dt.date.today()
    capturado_en = dt.datetime.now().isoformat(timespec="seconds")
    galpon, ciclo = parse_nombre_nave(os.path.basename(ruta_nave))
    registros = []

    for metrica, fuentes in CANONICAS.items():
        df, archivo = _elegir_fuente(ruta_nave, fuentes)
        if df is None:
            continue  # metrica sin datos en esta nave: no se inventa
        for _, fila in df.iterrows():
            fecha = fila["FECHA"].date()
            if fecha >= hoy:
                continue  # descarta dia en curso / no cerrado
            valor = pd.to_numeric(pd.Series([fila.get("NUM")]), errors="coerce").iloc[0]
            registros.append({
                "granja": granja,
                "galpon": galpon,
                "ciclo": ciclo,
                "metrica": metrica,
                "fecha_dato": fecha.isoformat(),
                "hora_cierre": _hora_cierre(fila.get("TIME")),
                "zona_horaria": zona_horaria,
                "capturado_en": capturado_en,
                "valor": None if pd.isna(valor) else int(valor),
                "edad_dia": int(fila["PRODDAY"]) if "PRODDAY" in df.columns and pd.notna(fila.get("PRODDAY")) else None,
                "semana": int(fila["PRODWEEK"]) if "PRODWEEK" in df.columns and pd.notna(fila.get("PRODWEEK")) else None,
                "fuente": archivo,
            })

    # Centinela: archivos con datos que NO estan en la lista canonica
    aviso = []
    for arch in sorted(os.listdir(ruta_nave)):
        if not arch.lower().endswith(".csv") or arch in ARCHIVOS_CONOCIDOS:
            continue
        df = _leer_df(os.path.join(ruta_nave, arch))
        if _tiene_datos(df):
            aviso.append(arch)

    return registros, aviso

def escanear(base_csv, granja, zona_horaria, hoy=None):
    """Recorre todas las naves. Devuelve (registros, avisos_por_nave)."""
    salida, avisos = [], {}
    if not os.path.isdir(base_csv):
        return salida, avisos
    for carpeta in sorted(os.listdir(base_csv)):
        ruta_nave = os.path.join(base_csv, carpeta)
        if not os.path.isdir(ruta_nave):
            continue
        galpon, _ = parse_nombre_nave(carpeta)
        if not galpon:
            continue
        try:
            regs, aviso = procesar_nave(ruta_nave, granja, zona_horaria, hoy=hoy)
        except Exception as e:
            # Un galpon roto no puede hacer perder los otros siete.
            log.error(f"[parser] fallo el galpon {carpeta}: {e}")
            continue
        salida.extend(regs)
        if aviso:
            avisos[carpeta] = aviso
    return salida, avisos
