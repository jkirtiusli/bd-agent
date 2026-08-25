# -*- coding: utf-8 -*-
"""
Parser + normalizador BD-Copy -> modelo canonico (v3).
- Lista canonica de metricas (metricas.py) con respaldo de fuentes.
- Campos temporales explicitos: fecha_dato, hora_cierre, zona_horaria, capturado_en.
- Descarta el dia en curso (solo dias cerrados).
- Centinela: detecta archivos con datos fuera de la lista canonica (los reporta, no los procesa).

Sin pandas: los CSV de BD-Copy son tabulados simples de unas pocas columnas y
el modulo `csv` de la stdlib alcanza. Sacarlo baja el .exe de Windows de ~80 MB
a ~10 MB y deja al Agente con una sola dependencia (pyyaml).

Se replica el comportamiento que tenia con pandas:
- `on_bad_lines="skip"`  -> se saltea la fila con mas campos que la cabecera
- filas mas cortas       -> se completan vacias (equivalente al NaN de pandas)
- `to_numeric(coerce)`   -> lo que no es numero queda en None (contaba como 0)
- `to_datetime(coerce)`  -> la fecha que no matchea %d.%m.%Y se descarta
"""
import os
import re
import csv
import logging
import datetime as dt

from bd_agent.metricas import CANONICAS, CLIMA, ARCHIVOS_CONOCIDOS

log = logging.getLogger("agente.parser")

_RE_NAVE = re.compile(r"MANBD_(Plc\d+_House\w+?)_(\d+)$", re.IGNORECASE)
_RE_FECHA = re.compile(r"\d{2}\.\d{2}\.\d{4}")

# Los CSV de BD-Copy vienen de un Windows viejo: latin-1 nunca falla al
# decodificar, asi que no hay riesgo de UnicodeDecodeError.
_ENCODING = "latin-1"


def parse_nombre_nave(carpeta):
    m = _RE_NAVE.search(carpeta)
    if not m:
        return None, None
    return m.group(1), m.group(2)


def _hora_cierre(time_str):
    """De '26/5/2026 22:00' saca '22:00'."""
    m = re.search(r"(\d{1,2}:\d{2})", str(time_str))
    return m.group(1) if m else None


def _numero(crudo):
    """
    Equivalente a pd.to_numeric(errors='coerce'): devuelve float o None.
    Vacio, texto o guion -> None (que en el conteo de datos vale 0).
    """
    if crudo is None:
        return None
    try:
        return float(str(crudo).strip())
    except (TypeError, ValueError):
        return None


def _entero(crudo):
    """Como _numero pero truncado a int, para edad_dia / semana / valor."""
    n = _numero(crudo)
    return None if n is None else int(n)


def _leer_tabla(ruta):
    """
    Lee un CSV tabulado y devuelve la lista de filas (dict por fila) que tienen
    una FECHA valida, ordenadas por fecha. Devuelve None si no sirve.

    Un archivo ilegible no puede tumbar la corrida entera: pasa de verdad que
    BD-Copy este escribiendo el CSV justo cuando el agente lo lee, o que quede
    truncado por un corte de luz. Antes eso propagaba la excepcion y se perdian
    TODOS los galpones.
    """
    try:
        with open(ruta, "r", encoding=_ENCODING, newline="") as f:
            lector = csv.reader(f, delimiter="\t")
            try:
                cabecera = next(lector)
            except StopIteration:
                return None  # archivo vacio
            if "DATE" not in cabecera:
                return None
            i_date = cabecera.index("DATE")
            ancho = len(cabecera)

            filas = []
            for campos in lector:
                if not campos or all(c == "" for c in campos):
                    continue                      # linea en blanco
                if len(campos) > ancho:
                    continue                      # on_bad_lines="skip"
                if len(campos) < ancho:
                    campos = campos + [""] * (ancho - len(campos))
                crudo = campos[i_date]
                if not _RE_FECHA.match(crudo):    # str.match: anclado al inicio
                    continue
                try:
                    fecha = dt.datetime.strptime(crudo, "%d.%m.%Y").date()
                except ValueError:
                    continue                      # errors="coerce" -> se descarta
                fila = dict(zip(cabecera, campos))
                fila["FECHA"] = fecha
                filas.append(fila)
    except OSError as e:
        log.warning(f"[parser] no se pudo abrir {os.path.basename(ruta)}: {e}")
        return None
    except Exception as e:
        log.warning(f"[parser] no se pudo leer {os.path.basename(ruta)}: {e}")
        return None

    if not filas:
        return None
    filas.sort(key=lambda f: f["FECHA"])  # estable, a diferencia de sort_values
    return filas


def _tiene_datos(filas):
    """Una fuente sirve si tiene al menos un NUM distinto de cero."""
    if not filas or "NUM" not in filas[0]:
        return False
    return any((_numero(f.get("NUM")) or 0) != 0 for f in filas)


def _elegir_fuente(ruta_nave, fuentes):
    """Devuelve (filas, archivo) de la primera fuente con datos; o (None, None)."""
    for arch in fuentes:
        ruta = os.path.join(ruta_nave, arch)
        if not os.path.exists(ruta):
            continue
        filas = _leer_tabla(ruta)
        if _tiene_datos(filas):
            return filas, arch
    return None, None


_AGREGADORES = {
    "prom": lambda valores: sum(valores) / len(valores),
    "min": min,
    "max": max,
}


def _hora_dato(fila):
    """
    La hora de la medicion, normalizada a 'HH:MM' (con cero adelante, porque
    va dentro de la clave y '1:00' y '01:00' serian dos datos distintos).
    Sale del TIME de la fila; si no trae hora, de la columna HOUR.
    """
    m = re.search(r"(\d{1,2}):(\d{2})", str(fila.get("TIME") or ""))
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    h = _entero(fila.get("HOUR"))
    return None if h is None else f"{h:02d}:00"


def _procesar_clima(ruta_nave, hoy, desde=None):
    """
    Registros horarios de clima/ventilacion de una nave:
    (metrica, archivo, fecha, hora, valor, fila). Cada fila del CSV es una hora;
    solo se combinan las sondas de la fila (prom/min/max), no se pierde la
    resolucion horaria. Los archivos anchos (AVG/MIN/MAX) se leen una sola vez
    aunque varias metricas salgan del mismo.
    """
    tablas = {}
    salida = []
    for metrica, spec in CLIMA.items():
        arch = spec["archivo"]
        if arch not in tablas:
            ruta = os.path.join(ruta_nave, arch)
            tablas[arch] = _leer_tabla(ruta) if os.path.exists(ruta) else None
        filas = tablas[arch]
        if not filas or spec["columnas"][0] not in filas[0]:
            continue  # sin archivo o con otra forma: no se inventa
        combinar = _AGREGADORES[spec["agregar"]]
        for fila in filas:
            fecha = fila["FECHA"]
            if fecha >= hoy:
                continue  # descarta dia en curso / no cerrado
            if desde is not None and fecha < desde:
                continue  # clima_desde: el historico viejo no se encola
            valores = []
            for col in spec["columnas"]:
                v = _numero(fila.get(col))
                if v is None or (spec["cero_es_nulo"] and v == 0):
                    continue
                valores.append(v)
            if not valores:
                continue  # fila sin ninguna sonda valida: no se inventa
            hora = _hora_dato(fila)
            if hora is None:
                continue  # sin hora no hay identidad: pisaria otras horas del dia
            salida.append((metrica, arch, fecha, hora, combinar(valores), fila))
    return salida


def procesar_nave(ruta_nave, granja, zona_horaria, hoy=None, clima_desde=None):
    """
    Devuelve (registros, aviso_centinela) para una nave.
    Descarta el dia en curso (fecha == hoy).
    """
    hoy = hoy or dt.date.today()
    capturado_en = dt.datetime.now().isoformat(timespec="seconds")
    galpon, ciclo = parse_nombre_nave(os.path.basename(ruta_nave))
    registros = []

    for metrica, fuentes in CANONICAS.items():
        filas, archivo = _elegir_fuente(ruta_nave, fuentes)
        if filas is None:
            continue  # metrica sin datos en esta nave: no se inventa
        for fila in filas:
            fecha = fila["FECHA"]
            if fecha >= hoy:
                continue  # descarta dia en curso / no cerrado
            registros.append({
                "granja": granja,
                "galpon": galpon,
                "ciclo": ciclo,
                "metrica": metrica,
                "fecha_dato": fecha.isoformat(),
                "hora_cierre": _hora_cierre(fila.get("TIME")),
                "zona_horaria": zona_horaria,
                "capturado_en": capturado_en,
                "valor": _entero(fila.get("NUM")),
                "edad_dia": _entero(fila.get("PRODDAY")),
                "semana": _entero(fila.get("PRODWEEK")),
                "fuente": archivo,
                "hora": None,  # las metricas de produccion son un valor por dia
            })

    # Clima y ventilacion: archivos anchos con una fila por hora. Cada hora es
    # un registro (con `hora`, que entra en la clave) y el valor va con
    # decimales: una temperatura no es una cuenta entera.
    for metrica, archivo, fecha, hora, valor, fila in \
            _procesar_clima(ruta_nave, hoy, desde=clima_desde):
        registros.append({
            "granja": granja,
            "galpon": galpon,
            "ciclo": ciclo,
            "metrica": metrica,
            "fecha_dato": fecha.isoformat(),
            "hora_cierre": None,
            "zona_horaria": zona_horaria,
            "capturado_en": capturado_en,
            "valor": round(valor, 2),
            "edad_dia": _entero(fila.get("PRODDAY")),
            "semana": _entero(fila.get("PRODWEEK")),
            "fuente": archivo,
            "hora": hora,
        })

    # Centinela: archivos con datos que NO estan en la lista canonica
    aviso = []
    for arch in sorted(os.listdir(ruta_nave)):
        if not arch.lower().endswith(".csv") or arch in ARCHIVOS_CONOCIDOS:
            continue
        if _tiene_datos(_leer_tabla(os.path.join(ruta_nave, arch))):
            aviso.append(arch)

    return registros, aviso


def escanear(base_csv, granja, zona_horaria, hoy=None, clima_desde=None):
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
            regs, aviso = procesar_nave(ruta_nave, granja, zona_horaria, hoy=hoy,
                                        clima_desde=clima_desde)
        except Exception as e:
            # Un galpon roto no puede hacer perder los otros siete.
            log.error(f"[parser] fallo el galpon {carpeta}: {e}")
            continue
        salida.extend(regs)
        if aviso:
            avisos[carpeta] = aviso
    return salida, avisos
