# -*- coding: utf-8 -*-
"""La cola es lo que hace que un corte de red no pierda datos."""
import pytest

from bd_agent import spool as bd_spool


def reg(metrica="huevos", fecha="2026-08-10", valor=100, galpon="Plc1_HouseA"):
    return {"granja": "g", "galpon": galpon, "ciclo": "7", "metrica": metrica,
            "fecha_dato": fecha, "hora_cierre": "22:00", "zona_horaria": "UTC",
            "capturado_en": "2026-08-11T12:30:00", "valor": valor,
            "edad_dia": 200, "semana": 29, "fuente": "X.csv"}


@pytest.fixture
def sp(tmp_path):
    with bd_spool.Spool(str(tmp_path / "spool.db")) as s:
        yield s


def test_clave_no_depende_de_capturado_en():
    a = reg()
    b = dict(reg(), capturado_en="2026-08-12T12:30:00")
    assert bd_spool.clave(a) == bd_spool.clave(b)
    # y el hash de valor tampoco: si no, cada corrida reenviaria todo
    assert bd_spool.valor_hash(a) == bd_spool.valor_hash(b)


def test_clave_distingue_metrica_y_dia():
    assert bd_spool.clave(reg()) != bd_spool.clave(reg(metrica="agua_dia"))
    assert bd_spool.clave(reg()) != bd_spool.clave(reg(fecha="2026-08-09"))


def test_encolar_y_tomar(sp):
    assert sp.encolar([reg(), reg(metrica="agua_dia")]) == 2
    bloque = sp.tomar(10)
    assert len(bloque) == 2
    # la clave viaja en el payload: el Core la usa como identidad del UPSERT
    assert all(p["clave"] for _k, _vh, p in bloque)


def test_no_reencola_lo_ya_confirmado(sp):
    sp.encolar([reg()])
    sp.confirmar([k for k, _, _ in sp.tomar(10)])
    # segunda corrida: el CSV trae lo mismo -> no genera trafico
    assert sp.encolar([reg()]) == 0
    assert sp.tomar(10) == []


def test_reencola_si_cambio_el_valor(sp):
    """Si rio arriba corrigen un numero, ese dato tiene que volver a viajar."""
    sp.encolar([reg(valor=100)])
    sp.confirmar([k for k, _, _ in sp.tomar(10)])
    assert sp.encolar([reg(valor=137)]) == 1
    assert sp.tomar(10)[0][2]["valor"] == 137


def test_confirmar_saca_de_pendientes(sp):
    sp.encolar([reg(), reg(metrica="agua_dia")])
    claves = [k for k, _, _ in sp.tomar(1)]
    sp.confirmar(claves)
    e = sp.estado()
    assert e["pendientes_en_cola"] == 1 and e["confirmados_total"] == 1


def test_tomar_con_offset_saltea(sp):
    sp.encolar([reg(fecha="2026-08-01"), reg(fecha="2026-08-02")])
    primero = sp.tomar(1)
    segundo = sp.tomar(1, offset=1)
    assert primero[0][0] != segundo[0][0]


def test_registrar_error_queda_en_el_estado(sp):
    sp.encolar([reg()])
    sp.registrar_error([k for k, _, _ in sp.tomar(10)], "HTTP 400 dato invalido")
    e = sp.estado()
    assert e["pendientes_en_cola"] == 1
    assert "400" in e["ultimo_error"] and e["max_intentos"] == 1


def test_reenviar_desde_una_fecha(sp):
    sp.encolar([reg(fecha="2026-08-01"), reg(fecha="2026-08-10")])
    sp.confirmar([k for k, _, _ in sp.tomar(10)])
    assert sp.estado()["pendientes_en_cola"] == 0
    assert sp.reencolar_desde("2026-08-05") == 1
    e = sp.estado()
    assert e["pendientes_en_cola"] == 1 and e["confirmados_total"] == 1


def test_estado_reporta_ultimo_dato(sp):
    sp.encolar([reg(fecha="2026-08-01"), reg(fecha="2026-08-10")])
    sp.confirmar([k for k, _, _ in sp.tomar(10)])
    assert sp.estado()["ultimo_dato_fecha"] == "2026-08-10"


def test_sobrevive_a_reabrir_el_archivo(tmp_path):
    """Un corte de luz a mitad de corrida no puede perder la cola."""
    ruta = str(tmp_path / "spool.db")
    with bd_spool.Spool(ruta) as s:
        s.encolar([reg()])
    with bd_spool.Spool(ruta) as s:
        assert s.estado()["pendientes_en_cola"] == 1
