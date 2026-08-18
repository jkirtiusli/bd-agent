# -*- coding: utf-8 -*-
"""El latido: nunca rompe la corrida, y lleva estado suficiente para diagnosticar."""
import os
import time

from bd_agent import __version__
from bd_agent.salud import _url_heartbeat, _construir_body, estado_origen, reunir_extra


def test_url_heartbeat_deriva_de_ingest():
    assert _url_heartbeat("https://core.flowkore.com/ingest") == \
        "https://core.flowkore.com/v1/agente/heartbeat"


def test_url_heartbeat_sin_ingest_usa_base():
    assert _url_heartbeat("https://core.flowkore.com/") == \
        "https://core.flowkore.com/v1/agente/heartbeat"


def test_construir_body():
    cfg = {"granja": "astillas", "zona_horaria": "America/Argentina/Buenos_Aires"}
    body = _construir_body(cfg, ok=True, registros=12, mensaje="ok")
    assert body["granja"] == "astillas"
    assert body["ok"] is True
    assert body["registros"] == 12
    assert body["mensaje"] == "ok"
    assert "agente_ts" in body and body["agente_ts"]  # ISO no vacío


def test_body_lleva_version_y_hostname():
    """Sin la version en el latido no hay forma de saber que granja quedo vieja."""
    body = _construir_body({"granja": "g"}, ok=True, registros=0, mensaje="x")
    assert body["version_agente"] == __version__
    assert body["hostname"]


def test_body_incluye_extra():
    body = _construir_body({"granja": "g"}, ok=True, registros=0, mensaje="x",
                           extra={"pendientes_en_cola": 42, "vacio": None})
    assert body["pendientes_en_cola"] == 42
    assert "vacio" not in body  # los None no viajan


def test_reportar_no_propaga_errores(monkeypatch):
    import bd_agent.salud as salud

    def explota(*a, **k):
        raise OSError("sin red")
    monkeypatch.setattr(salud.urllib.request, "urlopen", explota)
    cfg = {"granja": "g", "destino": {"modo": "http",
           "url": "https://core.flowkore.com/ingest", "token": "T"}}
    # no debe lanzar
    salud.reportar(cfg, ok=True, registros=1, mensaje="x")


def test_reportar_noop_si_no_http(monkeypatch):
    import bd_agent.salud as salud
    llamado = {"v": False}

    def marcar(*a, **k):
        llamado["v"] = True
        raise AssertionError("no deberia tocar la red")
    monkeypatch.setattr(salud.urllib.request, "urlopen", marcar)
    cfg = {"granja": "g", "destino": {"modo": "local_json", "ruta_salida": "x.json"}}
    salud.reportar(cfg, ok=True, registros=1, mensaje="x")  # no-op, no lanza
    assert llamado["v"] is False


# ---------------- estado del origen ----------------

def test_origen_inalcanzable(tmp_path):
    est = estado_origen(str(tmp_path / "no-existe"))
    assert est["origen_alcanzable"] is False


def test_frescura_y_galpones(tmp_path):
    for nave in ("MANBD_Plc1_HouseA_7", "MANBD_Plc1_HouseB_7"):
        d = tmp_path / nave
        d.mkdir()
        (d / "MANPRODUCTION_TODAYEGG.csv").write_text("x", encoding="utf-8")
    est = estado_origen(str(tmp_path))
    assert est["origen_alcanzable"] is True
    assert est["galpones_vistos"] == 2
    assert est["fuente_frescura_seg"] < 60


def test_frescura_detecta_csv_viejo(tmp_path):
    """Es el campo que distingue 'gateway caido' de 'BD-Copy dejo de exportar'."""
    d = tmp_path / "MANBD_Plc1_HouseA_7"
    d.mkdir()
    arch = d / "MANPRODUCTION_TODAYEGG.csv"
    arch.write_text("x", encoding="utf-8")
    viejo = time.time() - 3 * 24 * 3600
    os.utime(arch, (viejo, viejo))
    assert estado_origen(str(tmp_path))["fuente_frescura_seg"] > 2 * 24 * 3600


def test_reunir_extra_sin_spool(tmp_path):
    cfg = {"ruta_csv": str(tmp_path), "spool": {"ruta": str(tmp_path / "s.db")}}
    extra = reunir_extra(cfg, spool=None)
    assert extra["origen_alcanzable"] is True
    assert extra["disco_libre_mb"] > 0
