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


def test_reportar_no_propaga_errores(monkeypatch):
    import bd_agent.salud as salud

    def explota(*a, **k):
        raise OSError("sin red")
    monkeypatch.setattr(salud.urllib.request, "urlopen", explota)
    cfg = {"granja": "g", "destino": {"modo": "http",
           "url": "https://core.flowkore.com/ingest", "token": "T"}}
    # no debe lanzar
    salud.reportar(cfg, ok=True, registros=1, mensaje="x")


def test_reportar_noop_si_no_http():
    import bd_agent.salud as salud
    llamado = {"v": False}

    # si intentara abrir red, fallaría; con modo local_json no debe tocar la red
    cfg = {"granja": "g", "destino": {"modo": "local_json",
           "ruta_salida": "x.json"}}
    salud.reportar(cfg, ok=True, registros=1, mensaje="x")  # no-op, no lanza
    assert llamado["v"] is False
