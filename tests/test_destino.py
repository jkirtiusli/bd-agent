# -*- coding: utf-8 -*-
"""Reintentos, clasificacion de errores y drenaje de la cola."""
import email.message
import urllib.error

import pytest

from bd_agent import destino as bd_destino
from bd_agent import spool as bd_spool


DEST = {"modo": "http", "url": "https://core.test/ingest", "token": "T",
        "lote": 2, "reintentos": 3, "backoff_base": 0.01, "backoff_tope": 0.02}


class RespuestaFalsa:
    def __init__(self, status=200):
        self.status = status
        self.headers = email.message.Message()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(codigo, retry_after=None):
    cabeceras = email.message.Message()
    if retry_after is not None:
        cabeceras["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError("https://core.test/ingest", codigo,
                                  "boom", cabeceras, None)


def registros(n):
    return [{"clave": f"k{i}", "metrica": "huevos", "valor": i} for i in range(n)]


@pytest.fixture
def sin_dormir():
    esperas = []
    yield esperas, esperas.append


# ---------------- clasificacion de errores ----------------

def test_5xx_se_reintenta_y_termina_ok(monkeypatch, sin_dormir):
    esperas, dormir = sin_dormir
    intentos = []

    def urlopen(req, timeout=None):
        intentos.append(1)
        if len(intentos) < 3:
            raise http_error(503)
        return RespuestaFalsa(200)

    monkeypatch.setattr(bd_destino.urllib.request, "urlopen", urlopen)
    bd_destino.enviar_lote(registros(2), DEST, dormir=dormir)
    assert len(intentos) == 3
    assert len(esperas) == 2  # durmio entre intentos


def test_400_no_se_reintenta(monkeypatch, sin_dormir):
    _esperas, dormir = sin_dormir
    intentos = []

    def urlopen(req, timeout=None):
        intentos.append(1)
        raise http_error(400)

    monkeypatch.setattr(bd_destino.urllib.request, "urlopen", urlopen)
    with pytest.raises(bd_destino.ErrorPermanente):
        bd_destino.enviar_lote(registros(2), DEST, dormir=dormir)
    assert len(intentos) == 1  # insistir no lo arregla


def test_401_es_error_de_auth(monkeypatch, sin_dormir):
    _esperas, dormir = sin_dormir
    monkeypatch.setattr(bd_destino.urllib.request, "urlopen",
                        lambda req, timeout=None: (_ for _ in ()).throw(http_error(401)))
    with pytest.raises(bd_destino.ErrorAuth):
        bd_destino.enviar_lote(registros(1), DEST, dormir=dormir)


def test_sin_red_se_reintenta_y_se_agota(monkeypatch, sin_dormir):
    _esperas, dormir = sin_dormir
    intentos = []

    def urlopen(req, timeout=None):
        intentos.append(1)
        raise urllib.error.URLError("Network is unreachable")

    monkeypatch.setattr(bd_destino.urllib.request, "urlopen", urlopen)
    with pytest.raises(bd_destino.ErrorReintentable):
        bd_destino.enviar_lote(registros(1), DEST, dormir=dormir)
    assert len(intentos) == DEST["reintentos"]


def test_respeta_retry_after(monkeypatch, sin_dormir):
    esperas, dormir = sin_dormir
    intentos = []

    def urlopen(req, timeout=None):
        intentos.append(1)
        if len(intentos) == 1:
            raise http_error(429, retry_after=7)
        return RespuestaFalsa(200)

    monkeypatch.setattr(bd_destino.urllib.request, "urlopen", urlopen)
    bd_destino.enviar_lote(registros(1), DEST, dormir=dormir)
    assert esperas == [7.0]  # le hace caso al servidor, no al backoff propio


def test_clave_idempotencia_es_deterministica():
    a = bd_destino.clave_idempotencia(registros(3))
    b = bd_destino.clave_idempotencia(list(reversed(registros(3))))
    assert a == b  # el mismo lote reintentado manda la misma clave
    assert a != bd_destino.clave_idempotencia(registros(2))


def test_manda_cabeceras(monkeypatch, sin_dormir):
    _esperas, dormir = sin_dormir
    capturado = {}

    def urlopen(req, timeout=None):
        capturado["headers"] = dict(req.headers)
        return RespuestaFalsa(200)

    monkeypatch.setattr(bd_destino.urllib.request, "urlopen", urlopen)
    bd_destino.enviar_lote(registros(1), DEST, dormir=dormir)
    h = {k.lower(): v for k, v in capturado["headers"].items()}
    assert h["authorization"] == "Bearer T"
    assert h["idempotency-key"]


# ---------------- drenaje ----------------

@pytest.fixture
def sp(tmp_path):
    with bd_spool.Spool(str(tmp_path / "spool.db")) as s:
        yield s


def cargar(sp, n):
    sp.encolar([{"granja": "g", "galpon": "A", "ciclo": "1", "metrica": "huevos",
                 "fecha_dato": f"2026-08-{i + 1:02d}", "valor": i} for i in range(n)])


def test_drenar_confirma_lote_por_lote(monkeypatch, sp, sin_dormir):
    _esperas, dormir = sin_dormir
    cargar(sp, 5)
    monkeypatch.setattr(bd_destino.urllib.request, "urlopen",
                        lambda req, timeout=None: RespuestaFalsa(200))
    enviados, quedan, _msg = bd_destino.drenar(sp, DEST, dormir=dormir)
    assert (enviados, quedan) == (5, 0)
    assert sp.estado()["confirmados_total"] == 5


def test_drenar_conserva_lo_confirmado_aunque_despues_falle(monkeypatch, sp, sin_dormir):
    """Antes, un fallo en el lote 40 marcaba error y perdia el rastro de los 39."""
    _esperas, dormir = sin_dormir
    cargar(sp, 6)
    llamadas = []

    def urlopen(req, timeout=None):
        llamadas.append(1)
        if len(llamadas) <= 1:
            return RespuestaFalsa(200)
        raise urllib.error.URLError("se corto internet")

    monkeypatch.setattr(bd_destino.urllib.request, "urlopen", urlopen)
    enviados, quedan, _msg = bd_destino.drenar(sp, DEST, dormir=dormir)
    assert enviados == 2 and quedan == 4          # el primer lote entro y quedo firme
    assert sp.estado()["confirmados_total"] == 2  # no se reenvia la proxima vez


def test_drenar_no_gira_en_falso_con_un_lote_rechazado(monkeypatch, sp, sin_dormir):
    """
    Un lote con 400 queda en la cola para poder diagnosticarlo, pero no puede
    volver a tomarse en la misma pasada: seria un bucle infinito.
    """
    _esperas, dormir = sin_dormir
    cargar(sp, 4)
    llamadas = []

    def urlopen(req, timeout=None):
        llamadas.append(1)
        if len(llamadas) == 1:
            raise http_error(400)
        return RespuestaFalsa(200)

    monkeypatch.setattr(bd_destino.urllib.request, "urlopen", urlopen)
    enviados, quedan, msg = bd_destino.drenar(sp, DEST, dormir=dormir)
    assert enviados == 2 and quedan == 2   # 2 rechazados siguen en cola
    assert "rechazados" in msg
    assert "400" in sp.estado()["ultimo_error"]


def test_drenar_corta_si_el_token_es_invalido(monkeypatch, sp, sin_dormir):
    _esperas, dormir = sin_dormir
    cargar(sp, 6)
    llamadas = []

    def urlopen(req, timeout=None):
        llamadas.append(1)
        raise http_error(403)

    monkeypatch.setattr(bd_destino.urllib.request, "urlopen", urlopen)
    enviados, quedan, msg = bd_destino.drenar(sp, DEST, dormir=dormir)
    assert enviados == 0 and quedan == 6
    assert len(llamadas) == 1  # no martilla el Core con un token que no sirve
    assert "token rechazado" in msg


def test_drenar_con_cola_vacia(monkeypatch, sp, sin_dormir):
    _esperas, dormir = sin_dormir
    monkeypatch.setattr(bd_destino.urllib.request, "urlopen",
                        lambda req, timeout=None: RespuestaFalsa(200))
    assert bd_destino.drenar(sp, DEST, dormir=dormir)[:2] == (0, 0)


# ---------------- local ----------------

def test_local_json_escribe_atomico(tmp_path):
    salida = tmp_path / "sub" / "datos.json"
    msg = bd_destino.entregar(registros(3), {"modo": "local_json",
                                             "ruta_salida": str(salida)})
    assert salida.exists() and "3 registros" in msg
    assert not (tmp_path / "sub" / "datos.json.tmp").exists()
