from bd_agent.salud import _url_heartbeat, _construir_body


def test_url_heartbeat_deriva_de_ingest():
    assert _url_heartbeat("https://core.flowkore.com/ingest") == \
        "https://core.flowkore.com/v1/agente/heartbeat"


def test_url_heartbeat_sin_ingest_usa_base():
    # si la url no termina en /ingest, agrega el path al host
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
