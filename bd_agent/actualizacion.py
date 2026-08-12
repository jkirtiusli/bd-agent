# -*- coding: utf-8 -*-
"""
Auto-actualizacion por PULL.

El Agente pregunta si hay version nueva, la baja, la verifica y se reemplaza
solo. Nadie en la granja tiene que hacer nada.

Por que pull y no push: mandarle el archivo a una persona para que lo ejecute
reintroduce justo la dependencia que todo este proyecto viene a eliminar — que
alguien en la granja haga algo. Con pull, vos publicas una version y la flota
converge sola; y como el Agente reporta `version_agente` en cada latido, se ve
converger desde el tablero.

Por que el canal es el CORE y no GitHub: el Core ya sabe que version tiene cada
granja y ya tiene autenticacion por granja, asi que permite **canario de
verdad** (actualizar una granja primero) y fijar una version por granja sin
tocar nada en la granja. Ademas no depende de que GitHub sea alcanzable desde
la red de la granja.

Reglas de seguridad, todas obligatorias:
  - solo HTTPS
  - sha256 declarado en el manifiesto y verificado sobre lo bajado
  - tope de tamano
  - lo bajado tiene que ARRANCAR y reportar la version esperada ANTES de
    reemplazar nada
  - se guarda la version anterior; si la nueva no arranca, se vuelve sola
  - nunca se baja de version, salvo que este fijada explicitamente
"""
import os
import io
import ssl
import json
import shutil
import hashlib
import logging
import tarfile
import tempfile
import datetime as dt
import subprocess
import urllib.error
import urllib.request

from bd_agent import __version__

log = logging.getLogger("agente.actualizacion")

MAX_MB_POR_DEFECTO = 60
_TIMEOUT = 120
_SUFIJO_VIEJO = ".viejo"
_ARCHIVO_ESTADO = "actualizacion.json"


class ErrorActualizacion(Exception):
    """Cualquier problema al actualizar. Nunca debe frenar la entrega de datos."""


# --------------------------------------------------------------------------
# Versiones
# --------------------------------------------------------------------------

def parsear_version(texto):
    """'3.10.2' -> (3, 10, 2). Lo que no sea numero se ignora."""
    partes = []
    for trozo in str(texto or "").strip().lstrip("vV").split("."):
        digitos = ""
        for c in trozo:
            if not c.isdigit():
                break
            digitos += c
        partes.append(int(digitos) if digitos else 0)
    while len(partes) < 3:
        partes.append(0)
    return tuple(partes[:3])


def hay_que_actualizar(actual, disponible, version_fijada=None):
    """
    True si corresponde cambiar de version.

    Con `version_fijada` la granja queda clavada en esa version: se aplica
    incluso si es MAS VIEJA que la instalada (asi se revierte una flota entera
    desde el Core, sin entrar a ninguna granja).
    """
    if version_fijada:
        return parsear_version(actual) != parsear_version(version_fijada)
    return parsear_version(disponible) > parsear_version(actual)


# --------------------------------------------------------------------------
# Manifiesto
# --------------------------------------------------------------------------

def url_manifiesto(cfg):
    """Sale de la config, o se deriva del /ingest del Core como el heartbeat."""
    act = cfg.get("actualizacion") or {}
    if act.get("url"):
        return act["url"]
    destino = cfg.get("destino") or {}
    if destino.get("modo") != "http" or not destino.get("url"):
        return None
    u = destino["url"].rstrip("/")
    if u.endswith("/ingest"):
        u = u[: -len("/ingest")]
    return u + "/v1/agente/version"


def consultar(cfg):
    """
    Pide el manifiesto al canal. Devuelve el dict o None si no hay novedades
    ni canal. Nunca lanza por problemas de red: una actualizacion que no se
    puede consultar no es un incidente.

    Forma esperada:
        {"version": "3.1.0",
         "paquetes": {"ejecutable":  {"url": "https://...", "sha256": "..."},
                      "fuente_tar":  {"url": "https://...", "sha256": "..."}},
         "notas": "...", "version_fijada": "3.0.0"}
    """
    url = url_manifiesto(cfg)
    if not url:
        return None
    req = urllib.request.Request(url, method="GET")
    token = (cfg.get("destino") or {}).get("token", "")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    req.add_header("X-Agente-Version", __version__)
    req.add_header("X-Agente-Granja", str(cfg.get("granja", "")))
    req.add_header("X-Agente-Plataforma", plataforma())
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status == 204:
                return None  # el Core dice "no hay nada para vos"
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            log.debug("[actualizacion] el canal no tiene endpoint de version")
            return None
        log.warning(f"[actualizacion] HTTP {e.code} al consultar {url}")
        return None
    except (urllib.error.URLError, OSError, ValueError) as e:
        log.warning(f"[actualizacion] no se pudo consultar {url}: {e}")
        return None


def _paquete_para_esta_instalacion(manifiesto):
    """Elige el paquete segun como esta instalado el Agente."""
    paquetes = manifiesto.get("paquetes") or {}
    # La clave depende de COMO esta instalado, no del sistema operativo. El
    # Core sabe para que plataforma servir gracias a X-Agente-Plataforma.
    clave = "ejecutable" if es_ejecutable() else "fuente_tar"
    paquete = paquetes.get(clave)
    if not paquete:
        raise ErrorActualizacion(
            f"el manifiesto no trae paquete '{clave}' para esta instalacion")
    if not paquete.get("url") or not paquete.get("sha256"):
        raise ErrorActualizacion(f"el paquete '{clave}' no declara url y sha256")
    return paquete


def es_ejecutable():
    """True si corre empaquetado con PyInstaller (.exe), False si es codigo."""
    import sys
    return bool(getattr(sys, "frozen", False))


def plataforma():
    """Para que el Core sepa que paquete servir: 'windows-exe', 'linux-fuente'..."""
    import sys
    sistema = {"nt": "windows"}.get(os.name, sys.platform)
    return f"{sistema}-{'exe' if es_ejecutable() else 'fuente'}"


# --------------------------------------------------------------------------
# Descarga verificada
# --------------------------------------------------------------------------

def descargar(url, sha256_esperado, destino, max_mb=MAX_MB_POR_DEFECTO):
    """
    Baja a `destino` verificando sha256 y tope de tamano. HTTPS obligatorio.
    Si algo no cierra, borra lo bajado y lanza.
    """
    if not url.lower().startswith("https://"):
        raise ErrorActualizacion(f"la URL de descarga no es HTTPS: {url}")

    contexto = ssl.create_default_context()  # verifica certificado, siempre
    tope = max_mb * 1024 * 1024
    h = hashlib.sha256()
    bajado = 0
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT, context=contexto) as resp, \
                open(destino, "wb") as f:
            while True:
                bloque = resp.read(64 * 1024)
                if not bloque:
                    break
                bajado += len(bloque)
                if bajado > tope:
                    raise ErrorActualizacion(
                        f"la descarga supero {max_mb} MB: se aborta")
                h.update(bloque)
                f.write(bloque)
    except ErrorActualizacion:
        _borrar(destino)
        raise
    except (urllib.error.URLError, OSError) as e:
        _borrar(destino)
        raise ErrorActualizacion(f"no se pudo bajar {url}: {e}")

    real = h.hexdigest()
    if real.lower() != str(sha256_esperado).lower():
        _borrar(destino)
        raise ErrorActualizacion(
            f"el sha256 no coincide (esperado {sha256_esperado}, bajado {real})")
    return bajado


def _borrar(ruta):
    try:
        if os.path.isdir(ruta):
            shutil.rmtree(ruta, ignore_errors=True)
        elif os.path.exists(ruta):
            os.remove(ruta)
    except OSError:
        pass


# --------------------------------------------------------------------------
# Prueba de arranque
# --------------------------------------------------------------------------

def _probar(comando, version_esperada, cwd=None):
    """
    Corre `--version` sobre lo que se va a instalar. Si no arranca o reporta
    otra version, no se reemplaza nada.
    """
    try:
        r = subprocess.run(comando, capture_output=True, text=True,
                           timeout=120, cwd=cwd)
    except (OSError, subprocess.SubprocessError) as e:
        raise ErrorActualizacion(f"lo bajado no arranca: {e}")
    salida = f"{r.stdout} {r.stderr}".strip()
    if r.returncode != 0:
        raise ErrorActualizacion(f"lo bajado salio con codigo {r.returncode}: {salida}")
    if version_esperada and version_esperada.lstrip("vV") not in salida:
        raise ErrorActualizacion(
            f"lo bajado reporta '{salida}', se esperaba {version_esperada}")
    return salida


# --------------------------------------------------------------------------
# Aplicar
# --------------------------------------------------------------------------

def _ruta_ejecutable():
    import sys
    return os.path.abspath(sys.executable)


def aplicar_ejecutable(nuevo, version, ruta_actual=None):
    """
    Reemplaza el .exe en marcha.

    En Windows no se puede BORRAR un ejecutable en uso, pero si RENOMBRARLO:
    por eso se corre el actual a .viejo y se pone el nuevo en su lugar. El
    proceso que esta corriendo sigue con su copia; la proxima corrida ya usa
    la version nueva.
    """
    actual = os.path.abspath(ruta_actual or _ruta_ejecutable())
    viejo = actual + _SUFIJO_VIEJO

    if os.name != "nt":
        os.chmod(nuevo, 0o755)
    _probar([nuevo, "--version"], version)

    _borrar(viejo)
    os.replace(actual, viejo)
    try:
        os.replace(nuevo, actual)
    except OSError as e:
        os.replace(viejo, actual)  # dejar todo como estaba
        raise ErrorActualizacion(f"no se pudo poner la version nueva en su lugar: {e}")

    try:
        _probar([actual, "--version"], version)
    except ErrorActualizacion as e:
        # Ya instalada y no arranca: se vuelve sola, sin intervencion.
        os.replace(actual, nuevo)
        os.replace(viejo, actual)
        raise ErrorActualizacion(f"la version nueva no arranco, se revirtio: {e}")
    return viejo


def _buscar_paquete_en(carpeta):
    """Encuentra la carpeta que contiene bd_agent/__init__.py dentro de lo extraido."""
    for raiz, subdirs, _archivos in os.walk(carpeta):
        if os.path.basename(raiz) == "bd_agent" and \
                os.path.exists(os.path.join(raiz, "__init__.py")):
            return raiz
        if raiz.count(os.sep) - carpeta.count(os.sep) > 3:
            subdirs[:] = []
    raise ErrorActualizacion("el paquete bajado no contiene bd_agent/")


def aplicar_fuente(tar_bajado, version, destino_paquete=None):
    """
    Instalacion desde codigo (el gateway Linux): reemplaza la carpeta bd_agent/.

    No hace falta reiniciar nada: los servicios son `Type=oneshot` sobre timer,
    asi que la proxima corrida ya toma el codigo nuevo.
    """
    import bd_agent
    destino = os.path.abspath(destino_paquete or os.path.dirname(bd_agent.__file__))
    base = os.path.dirname(destino)
    viejo = destino + _SUFIJO_VIEJO

    tmp = tempfile.mkdtemp(prefix="bd-agent-act-", dir=base)
    try:
        with tarfile.open(tar_bajado, "r:*") as tar:
            try:
                tar.extractall(tmp, filter="data")   # bloquea path traversal
            except TypeError:                        # Python sin filtros
                _extraer_seguro(tar, tmp)
        paquete_nuevo = _buscar_paquete_en(tmp)
        _probar([_python(), "-m", "bd_agent.agente", "--version"], version,
                cwd=os.path.dirname(paquete_nuevo))

        _borrar(viejo)
        os.replace(destino, viejo)
        try:
            os.replace(paquete_nuevo, destino)
        except OSError as e:
            os.replace(viejo, destino)
            raise ErrorActualizacion(f"no se pudo instalar el paquete nuevo: {e}")

        try:
            _probar([_python(), "-m", "bd_agent.agente", "--version"], version, cwd=base)
        except ErrorActualizacion as e:
            _borrar(destino)
            os.replace(viejo, destino)
            raise ErrorActualizacion(f"la version nueva no arranco, se revirtio: {e}")
        return viejo
    finally:
        _borrar(tmp)


def _extraer_seguro(tar, destino):
    """Respaldo para Python sin filtros de tarfile: nada fuera del destino."""
    base = os.path.abspath(destino)
    for miembro in tar.getmembers():
        objetivo = os.path.abspath(os.path.join(base, miembro.name))
        if not objetivo.startswith(base + os.sep) and objetivo != base:
            raise ErrorActualizacion(f"el paquete intenta escribir fuera: {miembro.name}")
        if miembro.issym() or miembro.islnk():
            raise ErrorActualizacion(f"el paquete trae enlaces: {miembro.name}")
    tar.extractall(destino)


def _python():
    import sys
    return sys.executable


def revertir(ruta_actual=None):
    """Vuelve a la version guardada. Es el boton de panico."""
    if es_ejecutable():
        actual = os.path.abspath(ruta_actual or _ruta_ejecutable())
        viejo = actual + _SUFIJO_VIEJO
        if not os.path.exists(viejo):
            raise ErrorActualizacion(f"no hay version anterior guardada en {viejo}")
        respaldo = actual + ".revirtiendo"
        _borrar(respaldo)
        os.replace(actual, respaldo)
        os.replace(viejo, actual)
        _borrar(respaldo)
        return actual
    import bd_agent
    destino = os.path.abspath(os.path.dirname(bd_agent.__file__))
    viejo = destino + _SUFIJO_VIEJO
    if not os.path.exists(viejo):
        raise ErrorActualizacion(f"no hay version anterior guardada en {viejo}")
    _borrar(destino)
    os.replace(viejo, destino)
    return destino


# --------------------------------------------------------------------------
# Estado (viaja en el latido)
# --------------------------------------------------------------------------

def ruta_estado(cfg):
    base = os.path.dirname(os.path.abspath(
        (cfg.get("spool") or {}).get("ruta") or cfg.get("_ruta_config") or "."))
    return os.path.join(base or ".", _ARCHIVO_ESTADO)


def guardar_estado(cfg, **datos):
    datos["cuando"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        ruta = ruta_estado(cfg)
        os.makedirs(os.path.dirname(os.path.abspath(ruta)), exist_ok=True)
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False)
    except OSError as e:
        log.warning(f"[actualizacion] no se pudo guardar el estado: {e}")
    return datos


def leer_estado(cfg):
    try:
        with open(ruta_estado(cfg), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------
# Orquestacion
# --------------------------------------------------------------------------

def actualizar(cfg, log=log, revisar=False, desatendido=False):
    """
    Consulta, decide y aplica. Devuelve (cambio, mensaje).

    `revisar`     -> solo informa, no toca nada.
    `desatendido` -> lo corre el timer: respeta actualizacion.modo, que por
                     defecto es "manual". Se pasa a "automatica" cuando la
                     flota ya demostro que el circuito anda.
    """
    act = cfg.get("actualizacion") or {}
    if desatendido and act.get("modo", "manual") != "automatica":
        return False, "actualizacion automatica desactivada (actualizacion.modo)"

    manifiesto = consultar(cfg)
    if not manifiesto:
        return False, "el canal no informa ninguna version"

    disponible = manifiesto.get("version")
    # La version fijada puede venir del Core (por granja) o de la config local.
    fijada = manifiesto.get("version_fijada") or act.get("version_fijada")
    if not hay_que_actualizar(__version__, disponible, fijada):
        return False, f"ya esta en la version correcta ({__version__})"

    objetivo = fijada or disponible
    if revisar:
        return False, f"hay version disponible: {__version__} -> {objetivo}"

    # Todo lo que sigue va adentro del try: un manifiesto mal armado, una
    # descarga cortada o un binario que no arranca tienen que devolver
    # (False, motivo) y quedar registrados — nunca propagar una excepcion.
    # La entrega de datos no puede caerse por un problema de actualizacion.
    carpeta = tempfile.mkdtemp(prefix="bd-agent-descarga-")
    bajado = os.path.join(carpeta, "paquete.bin")
    try:
        paquete = _paquete_para_esta_instalacion(manifiesto)
        max_mb = int(act.get("max_mb", MAX_MB_POR_DEFECTO))
        log.info(f"[actualizacion] bajando {objetivo} desde {paquete['url']}")
        tamanio = descargar(paquete["url"], paquete["sha256"], bajado, max_mb)
        log.info(f"[actualizacion] {tamanio // 1024} KB verificados, instalando")
        if es_ejecutable():
            aplicar_ejecutable(bajado, objetivo)
        else:
            aplicar_fuente(bajado, objetivo)
    except ErrorActualizacion as e:
        guardar_estado(cfg, ok=False, version_anterior=__version__,
                       version_nueva=objetivo, detalle=str(e))
        log.error(f"[actualizacion] fallo: {e}")
        return False, f"fallo la actualizacion a {objetivo}: {e}"
    except Exception as e:  # red rara, disco lleno, permisos...
        guardar_estado(cfg, ok=False, version_anterior=__version__,
                       version_nueva=objetivo, detalle=f"{type(e).__name__}: {e}")
        log.exception(f"[actualizacion] error inesperado: {e}")
        return False, f"fallo la actualizacion a {objetivo}: {e}"
    finally:
        _borrar(carpeta)

    guardar_estado(cfg, ok=True, version_anterior=__version__,
                   version_nueva=objetivo, detalle="instalada")
    msg = f"actualizado {__version__} -> {objetivo}"
    log.info(f"[actualizacion] {msg}")
    return True, msg
