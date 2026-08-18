# -*- coding: utf-8 -*-
"""Ciclo completo: leer -> encolar -> drenar -> reportar."""
import logging
import sqlite3

import pytest

from bd_agent import agente
from bd_agent import destino as bd_destino
from bd_agent import config as bd_config
from bd_agent import spool as bd_spool


LOG = logging.getLogger("test")


def cfg_http(tmp_path, con_galpon=True):
    csv = tmp_path / "csv"
    csv.mkdir(exist_ok=True)
    if con_galpon:
        (csv / "MANBD_Plc1_HouseA_7").mkdir(exist_ok=True)
    return {"granja": "g", "zona_horaria": "UTC", "ruta_csv": str(csv),
            "destino": {"modo": "http", "url": "https://core.test/ingest",
                        "token": "t", "lote": 100},
            "spool": {"ruta": str(tmp_path / "spool.db")}}


def registro(fecha="2026-08-10", metrica="huevos", valor=1):
    return {"granja": "g", "galpon": "A", "ciclo": "1", "metrica": metrica,
            "fecha_dato": fecha, "valor": valor}


@pytest.fixture
def espiar(monkeypatch):
    """Reemplaza parser/destino/salud y devuelve lo que se reporto."""
    reportes = []

    def instalar(registros, drenar):
        monkeypatch.setattr(agente.bd_parser, "escanear",
                            lambda ruta, granja, zona: (registros, {}))
        monkeypatch.setattr(agente.bd_destino, "drenar", drenar)
        monkeypatch.setattr(agente.salud, "reportar",
                            lambda cfg, ok, registros, mensaje, extra=None:
                            reportes.append((ok, registros, mensaje, extra)))
    return instalar, reportes


def test_ciclo_encola_y_drena(tmp_path, espiar):
    instalar, reportes = espiar
    instalar([registro(), registro(metrica="agua_dia")],
             lambda sp, dest, dormir=None: (2, 0, "2 enviados", None))
    r = agente.ciclo_trabajo(cfg_http(tmp_path), LOG)
    assert r.ok is True and r.registros == 2 and r.codigo == bd_config.EXIT_OK
    assert reportes[0][0] is True
    # el heartbeat lleva el estado del origen y de la cola
    assert "pendientes_en_cola" in reportes[0][3]


def test_lo_leido_queda_en_la_cola(tmp_path, espiar):
    """El registro se persiste ANTES de intentar entregarlo. Esa es la garantia."""
    instalar, _reportes = espiar
    instalar([registro(), registro(metrica="agua_dia")],
             lambda sp, dest, dormir=None: (0, 2, "0 enviados, 2 en cola", None))
    agente.ciclo_trabajo(cfg_http(tmp_path), LOG)
    with bd_spool.Spool(str(tmp_path / "spool.db")) as sp:
        assert sp.estado()["pendientes_en_cola"] == 2


def test_core_caido_no_pierde_datos_y_marca_error(tmp_path, espiar):
    instalar, reportes = espiar
    instalar([registro()],
             lambda sp, dest, dormir=None: (0, 1, "0 enviados, 1 en cola",
                                            bd_destino.ErrorReintentable("core caido")))
    r = agente.ciclo_trabajo(cfg_http(tmp_path), LOG)
    assert r.ok is False and r.codigo == bd_config.EXIT_RED
    assert reportes[0][0] is False


def test_token_rechazado_da_codigo_de_auth(tmp_path, espiar):
    """El codigo sale del TIPO de error, no de buscar palabras en el mensaje."""
    instalar, _reportes = espiar
    instalar([registro()],
             lambda sp, dest, dormir=None: (0, 1, "0 enviados — corte: HTTP 403",
                                            bd_destino.ErrorAuth("HTTP 403")))
    assert agente.ciclo_trabajo(cfg_http(tmp_path), LOG).codigo == bd_config.EXIT_AUTH


def test_error_de_red_da_codigo_de_red_aunque_el_mensaje_hable_de_token(tmp_path, espiar):
    """La contracara: un mensaje que menciona el token pero no es un ErrorAuth."""
    instalar, _reportes = espiar
    instalar([registro()],
             lambda sp, dest, dormir=None: (0, 1, "0 enviados — corte: token rechazado?",
                                            bd_destino.ErrorReintentable("sin conexion")))
    assert agente.ciclo_trabajo(cfg_http(tmp_path), LOG).codigo == bd_config.EXIT_RED


def test_sin_registros_reporta_ok(tmp_path, espiar):
    instalar, reportes = espiar
    instalar([], lambda sp, dest, dormir=None: (0, 0, "0 enviados", None))
    r = agente.ciclo_trabajo(cfg_http(tmp_path), LOG)
    assert r.ok is True and r.mensaje == "sin registros nuevos"
    assert reportes[0][0] is True


def test_origen_caido_no_reporta_verde(tmp_path, monkeypatch):
    """
    Si el montaje SMB se cae, escanear() devuelve vacio sin lanzar nada. Sin
    este chequeo la granja se veria verde para siempre mientras deja de llegar
    el dato — la peor falla posible: silenciosa.
    """
    reportes = []
    monkeypatch.setattr(agente.salud, "reportar",
                        lambda cfg, ok, registros, mensaje, extra=None:
                        reportes.append((ok, mensaje)))
    monkeypatch.setattr(agente.bd_destino, "drenar",
                        lambda sp, dest, dormir=None: (0, 0, "0 enviados", None))
    cfg = cfg_http(tmp_path)
    cfg["ruta_csv"] = str(tmp_path / "montaje-caido")  # no existe
    r = agente.ciclo_trabajo(cfg, LOG)
    assert r.ok is False and r.codigo == bd_config.EXIT_ORIGEN
    assert reportes[0][0] is False and "no se puede leer el origen" in reportes[0][1]


def test_carpeta_sin_galpones_no_reporta_verde(tmp_path, monkeypatch):
    """
    La ruta apunta al lugar equivocado (eligieron la carpeta padre). La carpeta
    existe, asi que 'alcanzable' da True — pero no hay ni un galpon. Sin este
    chequeo se ve identico a "hoy no hubo datos nuevos".
    """
    reportes = []
    monkeypatch.setattr(agente.salud, "reportar",
                        lambda cfg, ok, registros, mensaje, extra=None:
                        reportes.append((ok, mensaje)))
    monkeypatch.setattr(agente.bd_destino, "drenar",
                        lambda sp, dest, dormir=None: (0, 0, "0 enviados", None))
    r = agente.ciclo_trabajo(cfg_http(tmp_path, con_galpon=False), LOG)
    assert r.ok is False and r.codigo == bd_config.EXIT_ORIGEN
    assert reportes[0][0] is False and "ningun galpon" in reportes[0][1]


def test_cola_se_drena_aunque_el_origen_este_caido(tmp_path, monkeypatch):
    """Lo que ya estaba encolado tiene que salir igual."""
    monkeypatch.setattr(agente.salud, "reportar",
                        lambda cfg, ok, registros, mensaje, extra=None: None)
    drenados = []
    monkeypatch.setattr(agente.bd_destino, "drenar",
                        lambda sp, dest, dormir=None: (drenados.append(1), (3, 0, "3 enviados", None))[1])
    cfg = cfg_http(tmp_path)
    cfg["ruta_csv"] = str(tmp_path / "montaje-caido")
    r = agente.ciclo_trabajo(cfg, LOG)
    assert drenados == [1] and r.registros == 3


def test_origen_ilegible_da_codigo_de_origen(tmp_path, monkeypatch):
    def explota(ruta, granja, zona):
        raise OSError("montaje SMB caido")
    monkeypatch.setattr(agente.bd_parser, "escanear", explota)
    reportes = []
    monkeypatch.setattr(agente.salud, "reportar",
                        lambda cfg, ok, registros, mensaje, extra=None:
                        reportes.append((ok, mensaje)))
    r = agente.ciclo_trabajo(cfg_http(tmp_path), LOG)
    assert r.codigo == bd_config.EXIT_ORIGEN and r.ok is False
    assert reportes[0][0] is False and "SMB" in reportes[0][1]


def test_segunda_corrida_no_reenvia_lo_mismo(tmp_path, espiar):
    """La razon por la que el trafico diario baja de 141k registros a decenas."""
    instalar, _reportes = espiar
    entregados = []

    def drenar(sp, dest, dormir=None):
        bloque = sp.tomar(1000)
        sp.confirmar([k for k, _, _ in bloque])
        entregados.append(len(bloque))
        return len(bloque), 0, f"{len(bloque)} enviados", None

    instalar([registro(), registro(metrica="agua_dia")], drenar)
    cfg = cfg_http(tmp_path)
    agente.ciclo_trabajo(cfg, LOG)
    agente.ciclo_trabajo(cfg, LOG)  # el CSV sigue teniendo lo mismo
    assert entregados == [2, 0]


def test_modo_local_json_no_usa_cola(tmp_path, monkeypatch):
    monkeypatch.setattr(agente.bd_parser, "escanear",
                        lambda ruta, granja, zona: ([registro()], {}))
    cfg = {"granja": "g", "zona_horaria": "UTC", "ruta_csv": str(tmp_path),
           "destino": {"modo": "local_json", "ruta_salida": str(tmp_path / "out.json")},
           "spool": {"ruta": str(tmp_path / "spool.db")}}
    r = agente.ciclo_trabajo(cfg, LOG)
    assert r.ok is True and (tmp_path / "out.json").exists()
    assert not (tmp_path / "spool.db").exists()


def test_solo_heartbeat_no_lee_los_csv(tmp_path, monkeypatch):
    def no_deberia(*a, **k):
        raise AssertionError("--solo-heartbeat no debe escanear los CSV")
    monkeypatch.setattr(agente.bd_parser, "escanear", no_deberia)
    reportes = []
    monkeypatch.setattr(agente.salud, "reportar",
                        lambda cfg, ok, registros, mensaje, extra=None:
                        reportes.append((ok, mensaje, extra)))
    agente.solo_heartbeat(cfg_http(tmp_path), LOG)
    assert reportes and reportes[0][2]["origen_alcanzable"] is True


# ---------------- la cola falla ----------------

def test_cola_bloqueada_no_tumba_el_ciclo(tmp_path, espiar, monkeypatch):
    """
    Los dos timers abren el mismo SQLite: se puede quedar bloqueado. Eso tiene
    que salir como Resultado con codigo, no como traceback y exit 1 generico.
    """
    instalar, reportes = espiar
    instalar([registro()], lambda sp, dest, dormir=None: (1, 0, "1 enviado", None))

    def cola_bloqueada(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(agente.bd_spool, "Spool", cola_bloqueada)
    r = agente.ciclo_trabajo(cfg_http(tmp_path), LOG)
    assert r.ok is False and r.codigo == bd_config.EXIT_ERROR
    assert "no se pudo usar la cola" in r.mensaje
    assert reportes and reportes[0][0] is False  # el Core se entera igual


def test_encolar_sin_disco_no_tumba_el_ciclo(tmp_path, espiar, monkeypatch):
    instalar, reportes = espiar
    instalar([registro()], lambda sp, dest, dormir=None: (1, 0, "1 enviado", None))

    def sin_disco(self, registros):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(agente.bd_spool.Spool, "encolar", sin_disco)
    r = agente.ciclo_trabajo(cfg_http(tmp_path), LOG)
    assert r.ok is False and r.codigo == bd_config.EXIT_ERROR
    assert reportes and reportes[0][0] is False


def test_solo_heartbeat_con_la_cola_bloqueada(tmp_path, monkeypatch):
    reportes = []
    monkeypatch.setattr(agente.salud, "reportar",
                        lambda cfg, ok, registros, mensaje, extra=None:
                        reportes.append((ok, mensaje)))

    def cola_bloqueada(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(agente.bd_spool, "Spool", cola_bloqueada)
    r = agente.solo_heartbeat(cfg_http(tmp_path), LOG)
    assert r.ok is False and r.codigo == bd_config.EXIT_ERROR
    assert reportes and reportes[0][0] is False


# ---------------- CLI ----------------

def test_main_config_invalida_devuelve_codigo_config(tmp_path):
    assert agente.main(["--config", str(tmp_path / "no.yaml"), "--once"]) \
        == bd_config.EXIT_CONFIG


def test_main_reenviar_desde(tmp_path, monkeypatch):
    ruta_cfg = tmp_path / "config.yaml"
    ruta_cfg.write_text(
        f'granja: g\nruta_csv: {bd_config.citar_yaml(tmp_path)}\n'
        f'destino:\n  modo: "http"\n  url: "https://core.test/ingest"\n  token: "t"\n',
        encoding="utf-8")
    with bd_spool.Spool(str(tmp_path / "spool.db")) as sp:
        sp.encolar([registro(fecha="2026-08-10")])
        sp.confirmar([k for k, _, _ in sp.tomar(10)])
    assert agente.main(["--config", str(ruta_cfg),
                        "--reenviar-desde", "2026-08-01"]) == bd_config.EXIT_OK
    with bd_spool.Spool(str(tmp_path / "spool.db")) as sp:
        assert sp.estado()["pendientes_en_cola"] == 1


def test_main_revertir_funciona_con_la_config_rota(tmp_path, monkeypatch):
    """
    Justo despues de una mala actualizacion la config puede estar ilegible. Si
    revertir necesitara cargarla, el rollback seria imposible: por eso va antes.
    """
    llamadas = []
    monkeypatch.setattr(agente.bd_actualizacion, "revertir",
                        lambda: llamadas.append(1) or str(tmp_path / "anterior"))
    ruta_cfg = tmp_path / "config.yaml"
    ruta_cfg.write_text("granja: [esto: no es\n  yaml valido", encoding="utf-8")
    assert agente.main(["--config", str(ruta_cfg), "--revertir"]) == bd_config.EXIT_OK
    assert llamadas == [1]


def test_main_revertir_funciona_sin_config(tmp_path, monkeypatch):
    monkeypatch.setattr(agente.bd_actualizacion, "revertir",
                        lambda: str(tmp_path / "anterior"))
    assert agente.main(["--config", str(tmp_path / "no-existe.yaml"),
                        "--revertir"]) == bd_config.EXIT_OK


def test_main_revertir_falla_devuelve_error(tmp_path, monkeypatch):
    def explota():
        raise agente.bd_actualizacion.ErrorActualizacion("no hay version anterior")
    monkeypatch.setattr(agente.bd_actualizacion, "revertir", explota)
    assert agente.main(["--config", str(tmp_path / "no-existe.yaml"),
                        "--revertir"]) == bd_config.EXIT_ERROR
