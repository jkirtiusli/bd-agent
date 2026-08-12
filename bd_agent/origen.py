# -*- coding: utf-8 -*-
"""
Elegir y validar la carpeta que BD-Copy usa para exportar.

Es el dato que mas se configura mal, y el que mas caro sale: si la ruta apunta
al lugar equivocado, el Agente no falla — simplemente no encuentra nada, y sin
un chequeo explicito la granja se ve "sin registros nuevos" para siempre.

Por eso aca no alcanza con "la carpeta existe". Validar significa **parsear de
verdad** una nave y mostrar cuantos galpones, que metricas y de que fecha se
leyeron. Si eso no da resultados, la ruta esta mal, aunque la carpeta exista.
"""
import os
import re
import shutil
import logging
import datetime as dt
import subprocess

from bd_agent.parser import parse_nombre_nave, procesar_nave

log = logging.getLogger("agente.origen")

# Carpetas que no tiene sentido recorrer al autodetectar.
_IGNORAR = {
    "windows", "$recycle.bin", "system volume information", "programdata",
    "node_modules", "__pycache__", ".git", "appdata", "winsxs", "temp", "tmp",
    "program files", "program files (x86)", "windowsapps", "$windows.~bt",
}
_MAX_DIRS = 20000   # techo de la busqueda: no puede colgarse recorriendo un disco
_MAX_PROF = 6


# --------------------------------------------------------------------------
# Validacion
# --------------------------------------------------------------------------

def validar(ruta, hoy=None):
    """
    Devuelve un informe de si esa carpeta sirve como origen.

    {ok, motivo, naves, csv_totales, frescura_seg, registros, metricas,
     fecha_mas_nueva}
    """
    informe = {"ruta": ruta, "ok": False, "motivo": "", "naves": [],
               "csv_totales": 0, "frescura_seg": None, "registros": 0,
               "metricas": [], "fecha_mas_nueva": None}

    if not ruta:
        informe["motivo"] = "no hay ninguna carpeta configurada"
        return informe
    if not os.path.exists(ruta):
        informe["motivo"] = "la carpeta no existe (¿unidad de red desconectada?)"
        return informe
    if not os.path.isdir(ruta):
        informe["motivo"] = "la ruta apunta a un archivo, no a una carpeta"
        return informe
    try:
        contenido = sorted(os.listdir(ruta))
    except OSError as e:
        informe["motivo"] = f"sin permiso para leer la carpeta: {e}"
        return informe

    naves = [c for c in contenido
             if os.path.isdir(os.path.join(ruta, c)) and parse_nombre_nave(c)[0]]
    informe["naves"] = naves
    if not naves:
        informe["motivo"] = ("la carpeta existe pero no tiene ningun galpon "
                             "(se esperan subcarpetas tipo MANBD_Plc1_HouseA_7)")
        return informe

    mas_nuevo = None
    for nave in naves:
        try:
            for arch in os.scandir(os.path.join(ruta, nave)):
                if not arch.is_file() or not arch.name.lower().endswith(".csv"):
                    continue
                informe["csv_totales"] += 1
                m = arch.stat().st_mtime
                if mas_nuevo is None or m > mas_nuevo:
                    mas_nuevo = m
        except OSError as e:
            informe["motivo"] = f"sin permiso para leer {nave}: {e}"
            return informe

    if not informe["csv_totales"]:
        informe["motivo"] = "hay galpones pero ninguno tiene archivos .csv"
        return informe
    informe["frescura_seg"] = int(max(0, dt.datetime.now().timestamp() - mas_nuevo))

    # La prueba de fuego: parsear de verdad. Que existan archivos no significa
    # que podamos leerlos ni que tengan datos utiles.
    try:
        registros, _aviso = procesar_nave(os.path.join(ruta, naves[0]),
                                          "prueba", "UTC", hoy=hoy)
    except Exception as e:
        informe["motivo"] = f"los archivos existen pero no se pudieron leer: {e}"
        return informe

    informe["registros"] = len(registros)
    informe["metricas"] = sorted({r["metrica"] for r in registros})
    if registros:
        informe["fecha_mas_nueva"] = max(r["fecha_dato"] for r in registros)
    if not registros:
        informe["motivo"] = (f"se leyo {naves[0]} pero no tiene ningun dato de dias "
                             f"cerrados (¿es una carpeta vieja o recien creada?)")
        return informe

    informe["ok"] = True
    informe["motivo"] = (f"{len(naves)} galpones, {informe['csv_totales']} archivos; "
                         f"se leyeron {len(registros)} registros de {naves[0]}")
    return informe


def describir(informe, imprimir=print):
    """Imprime el informe en castellano, para el instalador y el diagnostico."""
    if informe["ok"]:
        imprimir(f"  Carpeta valida: {informe['ruta']}")
        imprimir(f"  Galpones ({len(informe['naves'])}): "
                 f"{', '.join(informe['naves'][:6])}"
                 f"{' ...' if len(informe['naves']) > 6 else ''}")
        imprimir(f"  Archivos .csv: {informe['csv_totales']}")
        imprimir(f"  Prueba de lectura en {informe['naves'][0]}: "
                 f"{informe['registros']} registros, "
                 f"{len(informe['metricas'])} metricas")
        imprimir(f"  Metricas: {', '.join(informe['metricas'])}")
        imprimir(f"  Dato mas nuevo: {informe['fecha_mas_nueva']}")
        frescura = informe["frescura_seg"]
        if frescura is not None and frescura > 24 * 3600:
            imprimir(f"  AVISO: el archivo mas reciente tiene {frescura // 3600} h. "
                     f"Puede que BD-Copy no este exportando.")
    else:
        imprimir(f"  NO sirve: {informe['ruta']}")
        imprimir(f"  Motivo: {informe['motivo']}")


# --------------------------------------------------------------------------
# Autodeteccion
# --------------------------------------------------------------------------

def raices_probables():
    """Donde suele estar la carpeta de BD-Copy, de lo mas probable a lo menos."""
    raices = []
    perfil = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    for sub in ("OneDrive", "Desktop", "Escritorio", "Documents", "Documentos", ""):
        p = os.path.join(perfil, sub) if sub else perfil
        if os.path.isdir(p):
            raices.append(p)
    for fijo in (r"C:\BDCopy", r"C:\BD-Copy", r"C:\farmapi", r"D:\BDCopy", "/mnt", "/media"):
        if os.path.isdir(fijo):
            raices.append(fijo)
    if os.name == "nt":
        for letra in "CDEFG":
            unidad = f"{letra}:\\"
            if os.path.isdir(unidad):
                raices.append(unidad)
    # sin duplicados, conservando el orden de prioridad
    vistas, salida = set(), []
    for r in raices:
        clave = os.path.normcase(os.path.abspath(r))
        if clave not in vistas:
            vistas.add(clave)
            salida.append(r)
    return salida


def buscar_candidatas(raices=None, max_dirs=_MAX_DIRS, max_prof=_MAX_PROF):
    """
    Busca carpetas que contengan subcarpetas de galpon (MANBD_Plc*_House*_N).
    Acotada por profundidad y por cantidad de directorios: nunca se cuelga.
    """
    raices = raices if raices is not None else raices_probables()
    encontradas, vistos, visitados = [], set(), 0

    for raiz in raices:
        base_prof = raiz.rstrip(os.sep).count(os.sep)
        for actual, subdirs, _archivos in os.walk(raiz, topdown=True, onerror=None):
            visitados += 1
            if visitados > max_dirs:
                log.debug("[origen] busqueda cortada por limite de directorios")
                return encontradas
            if actual.rstrip(os.sep).count(os.sep) - base_prof >= max_prof:
                subdirs[:] = []
                continue
            subdirs[:] = [d for d in subdirs if d.lower() not in _IGNORAR
                          and not d.startswith(".")]
            if any(parse_nombre_nave(d)[0] for d in subdirs):
                clave = os.path.normcase(os.path.abspath(actual))
                if clave not in vistos:
                    vistos.add(clave)
                    encontradas.append(actual)
                subdirs[:] = []  # no hace falta bajar mas dentro de los galpones
    return encontradas


# --------------------------------------------------------------------------
# Selector grafico
# --------------------------------------------------------------------------

_PS_DIALOGO = """
Add-Type -AssemblyName System.Windows.Forms | Out-Null
$d = New-Object System.Windows.Forms.FolderBrowserDialog
$d.Description = "Elegi la carpeta donde BD-Copy deja los CSV (la que contiene las carpetas MANBD_...)"
$d.ShowNewFolderButton = $false
if ($env:BD_CARPETA_INICIAL) { $d.SelectedPath = $env:BD_CARPETA_INICIAL }
if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $d.SelectedPath }
"""


def elegir_carpeta_grafica(inicial=None):
    """
    Abre el explorador de carpetas de Windows. Devuelve la ruta o None si el
    usuario cancelo o no hay entorno grafico (por ejemplo, por SSH).
    """
    if os.name == "nt":
        entorno = dict(os.environ)
        if inicial:
            entorno["BD_CARPETA_INICIAL"] = inicial
        for exe in ("powershell", "pwsh"):
            if not shutil.which(exe):
                continue
            try:
                salida = subprocess.run(
                    [exe, "-NoProfile", "-STA", "-Command", _PS_DIALOGO],
                    capture_output=True, text=True, timeout=300, env=entorno)
                ruta = salida.stdout.strip()
                return ruta or None
            except (OSError, subprocess.SubprocessError) as e:
                log.debug(f"[origen] no se pudo abrir el dialogo con {exe}: {e}")
    try:  # fuera de Windows, o si PowerShell no esta
        import tkinter
        from tkinter import filedialog
        raiz = tkinter.Tk()
        raiz.withdraw()
        ruta = filedialog.askdirectory(
            title="Carpeta donde BD-Copy deja los CSV", initialdir=inicial or "/")
        raiz.destroy()
        return ruta or None
    except Exception as e:
        log.debug(f"[origen] sin selector grafico disponible: {e}")
        return None


# --------------------------------------------------------------------------
# Guardar la eleccion en el config
# --------------------------------------------------------------------------

_RE_RUTA_CSV = re.compile(r"^(\s*)ruta_csv\s*:.*$", re.MULTILINE)


def _yaml_comilla_simple(valor):
    """Comilla simple de YAML: no interpreta \\ , ideal para rutas de Windows."""
    return "'" + str(valor).replace("'", "''") + "'"


def guardar_ruta_csv(ruta_config, nueva_ruta):
    """
    Escribe ruta_csv en el config preservando comentarios y el resto del
    archivo (por eso se reemplaza la linea, en vez de re-serializar el YAML).
    Si el config no existe, se crea desde la plantilla.
    """
    if not os.path.exists(ruta_config):
        plantilla = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "config_ejemplo.yaml")
        carpeta = os.path.dirname(os.path.abspath(ruta_config))
        if carpeta:
            os.makedirs(carpeta, exist_ok=True)
        shutil.copyfile(plantilla, ruta_config)

    with open(ruta_config, "r", encoding="utf-8") as f:
        texto = f.read()

    linea = f"ruta_csv: {_yaml_comilla_simple(nueva_ruta)}"
    if _RE_RUTA_CSV.search(texto):
        texto = _RE_RUTA_CSV.sub(lambda m: m.group(1) + linea, texto, count=1)
    else:
        texto = texto.rstrip("\n") + "\n" + linea + "\n"

    tmp = ruta_config + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(texto)
    os.replace(tmp, ruta_config)  # atomico: nunca queda un config a medio escribir
    return ruta_config


# --------------------------------------------------------------------------
# Asistente interactivo
# --------------------------------------------------------------------------

def _ruta_actual(ruta_config):
    """Lee ruta_csv del config sin exigir que el resto del config sea valido."""
    try:
        with open(ruta_config, "r", encoding="utf-8") as f:
            m = _RE_RUTA_CSV.search(f.read())
    except OSError:
        return None
    if not m:
        return None
    valor = m.group(0).split(":", 1)[1].strip()
    if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "'\"":
        valor = valor[1:-1].replace("''", "'")
    return valor or None


def configurar_interactivo(ruta_config, imprimir=print, preguntar=input,
                           buscar=None, elegir=None):
    """
    Asistente para que quien instala el Agente elija la carpeta de BD-Copy
    sin tener que editar el YAML a mano. Devuelve 0 si quedo configurada.
    """
    buscar = buscar or buscar_candidatas
    elegir = elegir or elegir_carpeta_grafica

    imprimir("=" * 68)
    imprimir(" Configuracion del Agente BD-Copy — carpeta de datos")
    imprimir("=" * 68)

    actual = _ruta_actual(ruta_config)
    if actual:
        informe = validar(actual)
        imprimir("\nCarpeta configurada hoy:")
        describir(informe, imprimir)
        if informe["ok"]:
            resp = preguntar("\n¿Dejarla como esta? [S/n]: ").strip().lower()
            if resp in ("", "s", "si", "sí", "y"):
                imprimir("\nListo, no se cambio nada.")
                return 0

    imprimir("\nBuscando carpetas de BD-Copy en esta computadora...")
    candidatas = buscar()
    if candidatas:
        imprimir(f"Se encontraron {len(candidatas)}:")
        for i, c in enumerate(candidatas, 1):
            info = validar(c)
            marca = "OK " if info["ok"] else "?? "
            imprimir(f"  [{i}] {marca} {c}  ({len(info['naves'])} galpones)")
    else:
        imprimir("No se encontro ninguna automaticamente.")

    while True:
        opciones = "numero, " if candidatas else ""
        resp = preguntar(f"\nElegi ({opciones}B=buscar con el explorador, "
                         f"E=escribir la ruta, X=salir): ").strip()
        bajo = resp.lower()

        if bajo in ("x", "q", "salir"):
            imprimir("Cancelado: no se guardo nada.")
            return 1

        elegida = None
        if resp.isdigit() and candidatas and 1 <= int(resp) <= len(candidatas):
            elegida = candidatas[int(resp) - 1]
        elif bajo == "b":
            imprimir("Abriendo el explorador de carpetas...")
            elegida = elegir(candidatas[0] if candidatas else None)
            if not elegida:
                imprimir("No se eligio ninguna carpeta (o no hay entorno grafico).")
                continue
        elif bajo == "e":
            elegida = preguntar("Pega la ruta completa: ").strip().strip('"')
            if not elegida:
                continue
        else:
            imprimir("Opcion no valida.")
            continue

        imprimir(f"\nProbando {elegida} ...")
        informe = validar(elegida)
        describir(informe, imprimir)
        if not informe["ok"]:
            imprimir("\nEsa carpeta no sirve. Probemos otra.")
            imprimir("Pista: hay que elegir la carpeta que CONTIENE las carpetas")
            imprimir("       MANBD_Plc1_HouseA_7, MANBD_Plc1_HouseB_7, etc.")
            continue

        guardar_ruta_csv(ruta_config, elegida)
        imprimir(f"\nGuardada en {ruta_config}")
        imprimir("\nProbar la corrida completa con:")
        imprimir(f"  agente-bdcopy --config {ruta_config} --diagnostico")
        return 0
