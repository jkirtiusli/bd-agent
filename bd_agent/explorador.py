# -*- coding: utf-8 -*-
"""
`--explorar`: que datos hay disponibles en la granja, mas alla de los mapeados.

El centinela ya avisa en el log si aparece un CSV con datos fuera de la lista
canonica, pero solo dice el nombre. Cuando la pregunta es "¿podemos sacar
temperatura?" hace falta mas: que columnas trae, cuantas filas, de que fechas y
un valor de muestra. Con eso se decide si alcanza con agregar una linea en
metricas.py o si el archivo tiene otra forma y hay que extender el parser.

No entrega nada al Core ni toca la cola: es solo mirar.
"""
import os
import csv
import datetime as dt

from bd_agent.metricas import CANONICAS, CLIMA, ARCHIVOS_CONOCIDOS
from bd_agent.parser import parse_nombre_nave, _RE_FECHA, _numero, _ENCODING


def inspeccionar_archivo(ruta):
    """Radiografia de un CSV: columnas, filas, fechas y un valor de muestra."""
    info = {"nombre": os.path.basename(ruta), "columnas": [], "filas": 0,
            "filas_con_fecha": 0, "desde": None, "hasta": None,
            "numericas": {}, "error": None}
    try:
        with open(ruta, "r", encoding=_ENCODING, newline="") as f:
            lector = csv.reader(f, delimiter="\t")
            try:
                cabecera = next(lector)
            except StopIteration:
                info["error"] = "archivo vacio"
                return info
            info["columnas"] = cabecera
            i_date = cabecera.index("DATE") if "DATE" in cabecera else None
            ultimos = {}
            for campos in lector:
                if not campos or all(c == "" for c in campos):
                    continue
                info["filas"] += 1
                if len(campos) < len(cabecera):
                    campos = campos + [""] * (len(cabecera) - len(campos))
                if i_date is not None and _RE_FECHA.match(campos[i_date]):
                    try:
                        fecha = dt.datetime.strptime(campos[i_date], "%d.%m.%Y").date()
                    except ValueError:
                        continue
                    info["filas_con_fecha"] += 1
                    if info["desde"] is None or fecha < info["desde"]:
                        info["desde"] = fecha
                    if info["hasta"] is None or fecha > info["hasta"]:
                        info["hasta"] = fecha
                # ultimo valor no vacio de cada columna, para ver de que se trata
                for col, valor in zip(cabecera, campos):
                    if valor != "":
                        ultimos[col] = valor
    except OSError as e:
        info["error"] = f"no se pudo abrir: {e}"
        return info
    except Exception as e:
        info["error"] = f"no se pudo leer: {e}"
        return info

    # Columnas que traen numeros: son las candidatas a ser una metrica.
    for col, valor in ultimos.items():
        if col in ("DATE", "TIME"):
            continue
        if _numero(valor) is not None:
            info["numericas"][col] = valor
    return info


def explorar(base_csv):
    """Recorre todas las naves y junta lo que hay, agrupado por archivo."""
    informe = {"ruta": base_csv, "naves": [], "archivos": {}}
    if not os.path.isdir(base_csv):
        return informe

    for carpeta in sorted(os.listdir(base_csv)):
        ruta_nave = os.path.join(base_csv, carpeta)
        if not os.path.isdir(ruta_nave) or not parse_nombre_nave(carpeta)[0]:
            continue
        informe["naves"].append(carpeta)
        for arch in sorted(os.listdir(ruta_nave)):
            if not arch.lower().endswith(".csv"):
                continue
            info = inspeccionar_archivo(os.path.join(ruta_nave, arch))
            acumulado = informe["archivos"].setdefault(arch, {
                "nombre": arch, "mapeado": arch in ARCHIVOS_CONOCIDOS,
                "metrica": _metrica_de(arch), "en_naves": 0, "con_datos": 0,
                "columnas": [], "filas": 0, "desde": None, "hasta": None,
                "numericas": {}, "error": None,
            })
            acumulado["en_naves"] += 1
            acumulado["filas"] += info["filas_con_fecha"]
            if info["filas_con_fecha"]:
                acumulado["con_datos"] += 1
            if info["columnas"] and not acumulado["columnas"]:
                acumulado["columnas"] = info["columnas"]
            if info["numericas"]:
                acumulado["numericas"].update(info["numericas"])
            for clave, comparar in (("desde", min), ("hasta", max)):
                if info[clave] is not None:
                    actual = acumulado[clave]
                    acumulado[clave] = info[clave] if actual is None \
                        else comparar(actual, info[clave])
            if info["error"] and not acumulado["error"]:
                acumulado["error"] = info["error"]
    return informe


def _metrica_de(archivo):
    for metrica, fuentes in CANONICAS.items():
        if archivo in fuentes:
            return metrica
    clima = [m for m, spec in CLIMA.items() if spec["archivo"] == archivo]
    if clima:
        return ", ".join(clima)
    return None


def imprimir(informe, salida=print):
    """Informe en castellano. Lo sin mapear con datos va primero: es lo nuevo."""
    salida(f"Explorando {informe['ruta']}")
    if not informe["naves"]:
        salida("  No se encontro ningun galpon (MANBD_Plc*_House*_<ciclo>).")
        return
    salida(f"  {len(informe['naves'])} galpones: {', '.join(informe['naves'][:4])}"
           f"{' ...' if len(informe['naves']) > 4 else ''}")

    archivos = list(informe["archivos"].values())
    nuevos = [a for a in archivos if not a["mapeado"] and a["filas"]]
    mapeados = [a for a in archivos if a["mapeado"]]
    vacios = [a for a in archivos if not a["mapeado"] and not a["filas"]]

    salida("")
    salida("=" * 72)
    salida(f" SIN MAPEAR, CON DATOS ({len(nuevos)}) — esto se puede agregar")
    salida("=" * 72)
    if not nuevos:
        salida("  (ninguno: el agente ya esta levantando todo lo que trae datos)")
    for a in sorted(nuevos, key=lambda x: -x["filas"]):
        _detalle(a, salida)

    salida("")
    salida(f"YA MAPEADOS ({len(mapeados)}):")
    for a in sorted(mapeados, key=lambda x: x["nombre"]):
        estado = f"{a['con_datos']}/{a['en_naves']} galpones con datos"
        salida(f"  {a['nombre']:42s} -> {a['metrica']:20s} ({estado})")

    if vacios:
        salida("")
        salida(f"SIN MAPEAR Y SIN DATOS ({len(vacios)}): "
               f"{', '.join(sorted(a['nombre'] for a in vacios)[:8])}"
               f"{' ...' if len(vacios) > 8 else ''}")

    salida("")
    salida("Para agregar una de las de arriba: editar bd_agent/metricas.py")
    salida("y sumar una entrada al diccionario CANONICAS.")


def _detalle(a, salida):
    salida("")
    salida(f"  {a['nombre']}")
    salida(f"    en {a['con_datos']}/{a['en_naves']} galpones · {a['filas']} filas con fecha")
    if a["desde"]:
        salida(f"    fechas: {a['desde']} a {a['hasta']}")
    salida(f"    columnas: {', '.join(a['columnas']) or '(sin cabecera)'}")
    if a["numericas"]:
        muestra = ", ".join(f"{c}={v}" for c, v in list(a["numericas"].items())[:6])
        salida(f"    ultimos valores: {muestra}")
    # El parser actual saca el valor de la columna NUM. Si el archivo no la
    # tiene, no alcanza con agregarlo a CANONICAS: hay que extender el parser.
    if "NUM" not in a["columnas"]:
        salida("    OJO: no tiene columna NUM — no alcanza con mapearlo, "
               "hay que extender el parser")
    if a["error"]:
        salida(f"    error: {a['error']}")


def correr(ruta_config, salida=print):
    """Punto de entrada de --explorar. Devuelve el codigo de salida."""
    from bd_agent import config as bd_config
    try:
        cfg = bd_config.cargar(ruta_config)
    except bd_config.ErrorConfig as e:
        salida(f"config invalida: {e}")
        return bd_config.EXIT_CONFIG
    informe = explorar(cfg["ruta_csv"])
    imprimir(informe, salida)
    return bd_config.EXIT_OK if informe["naves"] else bd_config.EXIT_ORIGEN
