# -*- coding: utf-8 -*-
"""
Auto-actualizacion por pull.

Es el codigo donde un bug se paga mas caro: una actualizacion mala rompe TODAS
las granjas a la vez y sin acceso remoto. Por eso se testea sobre todo lo que
tiene que pasar cuando algo sale mal.
"""
import io
import os
import json
import pathlib
import hashlib
import tarfile
import urllib.error

import pytest

from bd_agent import actualizacion as act


def sha(datos):
    return hashlib.sha256(datos).hexdigest()


def cfg_base(tmp_path, **extra):
    cfg = {"granja": "g",
           "destino": {"modo": "http", "url": "https://core.test/ingest", "token": "T"},
           "spool": {"ruta": str(tmp_path / "spool.db")},
           "actualizacion": {}}
    cfg["actualizacion"].update(extra)
    return cfg


# ---------------- versiones ----------------

@pytest.mark.parametrize("texto,esperado", [
    ("3.0.0", (3, 0, 0)), ("v3.1.2", (3, 1, 2)), ("3.1", (3, 1, 0)),
    ("3", (3, 0, 0)), ("3.0.0-rc1", (3, 0, 0)), ("", (0, 0, 0)), (None, (0, 0, 0)),
])
def test_parsear_version(texto, esperado):
    assert act.parsear_version(texto) == esperado


def test_compara_numericamente_no_alfabeticamente():
    """'3.10.0' > '3.9.0' — comparado como texto daria al reves."""
    assert act.parsear_version("3.10.0") > act.parsear_version("3.9.0")
    assert act.hay_que_actualizar("3.9.0", "3.10.0") is True


def test_no_actualiza_a_la_misma_ni_a_una_vieja():
    assert act.hay_que_actualizar("3.0.0", "3.0.0") is False
    assert act.hay_que_actualizar("3.1.0", "3.0.0") is False


def test_version_fijada_permite_bajar():
    """Asi se revierte una flota entera desde el Core, sin entrar a la granja."""
    assert act.hay_que_actualizar("3.1.0", "3.1.0", version_fijada="3.0.0") is True
    assert act.hay_que_actualizar("3.0.0", "3.1.0", version_fijada="3.0.0") is False


# ---------------- manifiesto ----------------

def test_url_se_deriva_del_core(tmp_path):
    assert act.url_manifiesto(cfg_base(tmp_path)) == "https://core.test/v1/agente/version"


def test_url_explicita_gana(tmp_path):
    cfg = cfg_base(tmp_path, url="https://otro.test/manifiesto.json")
    assert act.url_manifiesto(cfg) == "https://otro.test/manifiesto.json"


def test_sin_canal_no_hay_url(tmp_path):
    cfg = cfg_base(tmp_path)
    cfg["destino"] = {"modo": "local_json", "ruta_salida": "x.json"}
    assert act.url_manifiesto(cfg) is None
    assert act.consultar(cfg) is None


def test_consultar_no_propaga_errores_de_red(tmp_path, monkeypatch):
    """Que no se pueda consultar la actualizacion no es un incidente."""
    monkeypatch.setattr(act.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(
                            urllib.error.URLError("sin red")))
    assert act.consultar(cfg_base(tmp_path)) is None


def test_consultar_404_es_silencioso(tmp_path, monkeypatch):
    """El Core todavia no implemento el endpoint: no es un error."""
    def explota(*a, **k):
        raise urllib.error.HTTPError("u", 404, "no", None, None)
    monkeypatch.setattr(act.urllib.request, "urlopen", explota)
    assert act.consultar(cfg_base(tmp_path)) is None


# ---------------- descarga verificada ----------------

class RespuestaFalsa(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def servir(monkeypatch):
    def hacer(datos):
        monkeypatch.setattr(act.urllib.request, "urlopen",
                            lambda *a, **k: RespuestaFalsa(datos))
    return hacer


def test_descarga_verifica_sha256(tmp_path, servir):
    datos = b"contenido del paquete"
    servir(datos)
    destino = str(tmp_path / "bajado.bin")
    assert act.descargar("https://x/y", sha(datos), destino) == len(datos)
    assert open(destino, "rb").read() == datos


def test_sha256_que_no_coincide_borra_lo_bajado(tmp_path, servir):
    """Un binario alterado en transito no puede quedar en disco."""
    servir(b"paquete manipulado")
    destino = str(tmp_path / "bajado.bin")
    with pytest.raises(act.ErrorActualizacion, match="sha256"):
        act.descargar("https://x/y", sha(b"lo que esperabamos"), destino)
    assert not os.path.exists(destino)


def test_rechaza_http_sin_tls(tmp_path):
    with pytest.raises(act.ErrorActualizacion, match="HTTPS"):
        act.descargar("http://x/y", "abc", str(tmp_path / "z.bin"))


def test_corta_si_supera_el_tope(tmp_path, servir):
    servir(b"x" * (3 * 1024 * 1024))
    destino = str(tmp_path / "grande.bin")
    with pytest.raises(act.ErrorActualizacion, match="supero"):
        act.descargar("https://x/y", "loquesea", destino, max_mb=1)
    assert not os.path.exists(destino)


# ---------------- reemplazo del ejecutable ----------------

def falso_exe(ruta, version, ok=True):
    """
    Un 'ejecutable' de mentira que imprime su version (o falla).

    En Windows tiene que ser un .bat: un script con shebang de shell da
    [WinError 193] "%1 is not a valid Win32 application". Devuelve la ruta
    real, que puede no ser la que se paso.
    """
    ruta = pathlib.Path(ruta)
    if os.name == "nt":
        ruta = ruta.with_suffix(".bat")
        contenido = "@echo off\r\n" + (f"echo agente-bd-copy {version}\r\n" if ok
                                       else "echo roto 1>&2\r\nexit /b 1\r\n")
    else:
        contenido = "#!/bin/sh\n" + (f"echo 'agente-bd-copy {version}'\n" if ok
                                    else "echo 'roto' >&2\nexit 1\n")
    ruta.write_text(contenido, encoding="utf-8")
    os.chmod(ruta, 0o755)
    return ruta


def test_reemplaza_y_guarda_la_anterior(tmp_path):
    actual = falso_exe(tmp_path / "agente.exe", "3.0.0")
    nuevo = falso_exe(tmp_path / "nuevo.bin", "3.1.0")

    viejo = act.aplicar_ejecutable(str(nuevo), "3.1.0", ruta_actual=str(actual))
    assert "3.1.0" in actual.read_text()
    assert os.path.exists(viejo) and "3.0.0" in open(viejo).read()


def test_no_reemplaza_si_lo_bajado_no_arranca(tmp_path):
    """La verificacion es ANTES de tocar nada: el original queda intacto."""
    actual = falso_exe(tmp_path / "agente.exe", "3.0.0")
    nuevo = falso_exe(tmp_path / "nuevo.bin", "3.1.0", ok=False)

    with pytest.raises(act.ErrorActualizacion):
        act.aplicar_ejecutable(str(nuevo), "3.1.0", ruta_actual=str(actual))
    assert "3.0.0" in actual.read_text()
    assert not os.path.exists(str(actual) + ".viejo")


def test_no_reemplaza_si_reporta_otra_version(tmp_path):
    """Defensa contra un manifiesto que apunta al binario equivocado."""
    actual = falso_exe(tmp_path / "agente.exe", "3.0.0")
    nuevo = falso_exe(tmp_path / "nuevo.bin", "2.9.0")

    with pytest.raises(act.ErrorActualizacion, match="se esperaba"):
        act.aplicar_ejecutable(str(nuevo), "3.1.0", ruta_actual=str(actual))
    assert "3.0.0" in actual.read_text()


def test_revertir_vuelve_a_la_anterior(tmp_path, monkeypatch):
    actual = falso_exe(tmp_path / "agente.exe", "3.0.0")
    nuevo = falso_exe(tmp_path / "nuevo.bin", "3.1.0")
    act.aplicar_ejecutable(str(nuevo), "3.1.0", ruta_actual=str(actual))

    monkeypatch.setattr(act, "es_ejecutable", lambda: True)
    act.revertir(ruta_actual=str(actual))
    assert "3.0.0" in actual.read_text()


def test_revertir_sin_version_anterior_avisa(tmp_path, monkeypatch):
    actual = falso_exe(tmp_path / "agente.exe", "3.0.0")
    monkeypatch.setattr(act, "es_ejecutable", lambda: True)
    with pytest.raises(act.ErrorActualizacion, match="no hay version anterior"):
        act.revertir(ruta_actual=str(actual))


# ---------------- paquete de codigo (gateway) ----------------

def tar_con(tmp_path, arbol, nombre="paquete.tar.gz"):
    ruta = tmp_path / nombre
    with tarfile.open(ruta, "w:gz") as tar:
        for camino, contenido in arbol.items():
            datos = contenido.encode("utf-8")
            info = tarfile.TarInfo(camino)
            info.size = len(datos)
            tar.addfile(info, io.BytesIO(datos))
    return str(ruta)


def test_rechaza_tar_sin_el_paquete(tmp_path):
    malo = tar_con(tmp_path, {"otracosa/README.md": "nada"})
    with pytest.raises(act.ErrorActualizacion, match="no contiene bd_agent"):
        act.aplicar_fuente(malo, "3.1.0", destino_paquete=str(tmp_path / "bd_agent"))


def test_rechaza_tar_con_path_traversal(tmp_path):
    """Un tar que intenta escribir fuera del destino no se extrae."""
    ruta = tmp_path / "malicioso.tar.gz"
    with tarfile.open(ruta, "w:gz") as tar:
        datos = b"pwned"
        info = tarfile.TarInfo("../../etc/pwned")
        info.size = len(datos)
        tar.addfile(info, io.BytesIO(datos))
    with pytest.raises(Exception):
        act.aplicar_fuente(str(ruta), "3.1.0",
                           destino_paquete=str(tmp_path / "bd_agent"))
    assert not os.path.exists(tmp_path.parent.parent / "etc" / "pwned")


# ---------------- orquestacion ----------------

@pytest.fixture
def manifiesto(monkeypatch):
    def servir(datos):
        monkeypatch.setattr(act, "consultar", lambda cfg: datos)
    return servir


def test_sin_canal_no_hace_nada(tmp_path, manifiesto):
    manifiesto(None)
    cambio, msg = act.actualizar(cfg_base(tmp_path))
    assert cambio is False and "no informa" in msg


def test_ya_esta_al_dia(tmp_path, manifiesto):
    manifiesto({"version": act.__version__})
    cambio, msg = act.actualizar(cfg_base(tmp_path))
    assert cambio is False and "version correcta" in msg


def test_revisar_no_instala(tmp_path, manifiesto, monkeypatch):
    manifiesto({"version": "99.0.0"})
    llamadas = []
    monkeypatch.setattr(act, "descargar", lambda *a, **k: llamadas.append(1))
    cambio, msg = act.actualizar(cfg_base(tmp_path), revisar=True)
    assert cambio is False and "99.0.0" in msg and llamadas == []


def test_desatendido_respeta_modo_manual(tmp_path, manifiesto, monkeypatch):
    """El timer consulta pero no aplica hasta que se abra a 'automatica'."""
    consultas = []
    monkeypatch.setattr(act, "consultar", lambda cfg: consultas.append(1))
    cambio, msg = act.actualizar(cfg_base(tmp_path, modo="manual"), desatendido=True)
    assert cambio is False and "desactivada" in msg
    assert consultas == []  # ni siquiera consulta


def test_desatendido_aplica_si_es_automatica(tmp_path, manifiesto, monkeypatch):
    manifiesto({"version": "99.0.0",
                "paquetes": {"fuente_tar": {"url": "https://x/y", "sha256": "abc"}}})
    aplicados = []
    monkeypatch.setattr(act, "descargar", lambda *a, **k: 10)
    monkeypatch.setattr(act, "es_ejecutable", lambda: False)
    monkeypatch.setattr(act, "aplicar_fuente",
                        lambda bajado, version, **k: aplicados.append(version))
    cambio, msg = act.actualizar(cfg_base(tmp_path, modo="automatica"), desatendido=True)
    assert cambio is True and aplicados == ["99.0.0"] and "99.0.0" in msg


def test_manifiesto_sin_sha256_se_rechaza(tmp_path, manifiesto, monkeypatch):
    manifiesto({"version": "99.0.0",
                "paquetes": {"fuente_tar": {"url": "https://x/y"}}})
    monkeypatch.setattr(act, "es_ejecutable", lambda: False)
    cambio, msg = act.actualizar(cfg_base(tmp_path))
    assert cambio is False and "sha256" in msg


def test_manifiesto_sin_paquete_para_esta_instalacion(tmp_path, manifiesto, monkeypatch):
    manifiesto({"version": "99.0.0",
                "paquetes": {"ejecutable": {"url": "https://x/y", "sha256": "a"}}})
    monkeypatch.setattr(act, "es_ejecutable", lambda: False)  # corre desde codigo
    cambio, msg = act.actualizar(cfg_base(tmp_path))
    assert cambio is False and "fuente_tar" in msg


def test_un_fallo_queda_registrado_para_el_latido(tmp_path, manifiesto, monkeypatch):
    """Si una granja queda trabada actualizando, tiene que verse en el tablero."""
    manifiesto({"version": "99.0.0",
                "paquetes": {"fuente_tar": {"url": "https://x/y", "sha256": "abc"}}})
    monkeypatch.setattr(act, "es_ejecutable", lambda: False)
    monkeypatch.setattr(act, "descargar", lambda *a, **k: (_ for _ in ()).throw(
        act.ErrorActualizacion("el sha256 no coincide")))
    cfg = cfg_base(tmp_path)
    cambio, _msg = act.actualizar(cfg)
    assert cambio is False
    estado = act.leer_estado(cfg)
    assert estado["ok"] is False and "sha256" in estado["detalle"]
    assert estado["version_nueva"] == "99.0.0" and estado["cuando"]


def test_exito_queda_registrado(tmp_path, manifiesto, monkeypatch):
    manifiesto({"version": "99.0.0",
                "paquetes": {"fuente_tar": {"url": "https://x/y", "sha256": "abc"}}})
    monkeypatch.setattr(act, "es_ejecutable", lambda: False)
    monkeypatch.setattr(act, "descargar", lambda *a, **k: 10)
    monkeypatch.setattr(act, "aplicar_fuente", lambda *a, **k: "viejo")
    cfg = cfg_base(tmp_path)
    act.actualizar(cfg)
    estado = act.leer_estado(cfg)
    assert estado["ok"] is True and estado["version_nueva"] == "99.0.0"


def test_estado_ausente_no_rompe(tmp_path):
    assert act.leer_estado(cfg_base(tmp_path)) is None
