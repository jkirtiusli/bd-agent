# -*- coding: utf-8 -*-
"""
Robustez del parser: un archivo o un galpon roto no puede hacer perder la corrida.

Pasa de verdad en la granja: BD-Copy escribiendo el CSV justo cuando el agente
lo lee, o un archivo truncado por un corte de luz.
"""
import datetime as dt

from bd_agent import parser as bd_parser


CABECERA = "DATE\tTIME\tNUM\tPRODDAY\tPRODWEEK"


def nave(tmp_path, nombre="MANBD_Plc1_HouseA_7", archivos=None):
    d = tmp_path / nombre
    d.mkdir(exist_ok=True)
    ayer = dt.date.today() - dt.timedelta(days=1)
    for arch, contenido in (archivos or {}).items():
        if contenido is None:  # archivo valido por defecto
            contenido = (f"{CABECERA}\n{ayer.strftime('%d.%m.%Y')}\t"
                         f"{ayer.strftime('%d/%m/%Y')} 22:00\t15000\t200\t29")
        (d / arch).write_text(contenido, encoding="latin-1")
    return d


def test_parsea_una_nave(tmp_path):
    nave(tmp_path, archivos={"MANPRODUCTION_TODAYEGG.csv": None})
    registros, _avisos = bd_parser.escanear(str(tmp_path), "g", "UTC")
    assert len(registros) == 1
    r = registros[0]
    assert (r["galpon"], r["ciclo"], r["metrica"], r["valor"]) == \
        ("Plc1_HouseA", "7", "huevos", 15000)
    assert r["fecha_dato"] == (dt.date.today() - dt.timedelta(days=1)).isoformat()
    assert r["hora_cierre"] == "22:00"


def test_descarta_el_dia_en_curso(tmp_path):
    hoy = dt.date.today()
    nave(tmp_path, archivos={"MANPRODUCTION_TODAYEGG.csv":
         f"{CABECERA}\n{hoy.strftime('%d.%m.%Y')}\thoy 10:00\t999\t201\t29"})
    registros, _ = bd_parser.escanear(str(tmp_path), "g", "UTC")
    assert registros == []


def test_archivo_vacio_no_rompe_la_corrida(tmp_path):
    """pandas tira EmptyDataError, que no es OSError: antes propagaba y mataba todo."""
    nave(tmp_path, "MANBD_Plc1_HouseA_7", {"MANPRODUCTION_TODAYEGG.csv": ""})
    nave(tmp_path, "MANBD_Plc1_HouseB_7", {"MANPRODUCTION_TODAYEGG.csv": None})
    registros, _ = bd_parser.escanear(str(tmp_path), "g", "UTC")
    assert [r["galpon"] for r in registros] == ["Plc1_HouseB"]


def test_archivo_basura_no_rompe_la_corrida(tmp_path):
    nave(tmp_path, "MANBD_Plc1_HouseA_7",
         {"MANPRODUCTION_TODAYEGG.csv": "\x00\x01basura sin columnas\x02"})
    nave(tmp_path, "MANBD_Plc1_HouseB_7", {"MANPRODUCTION_TODAYEGG.csv": None})
    registros, _ = bd_parser.escanear(str(tmp_path), "g", "UTC")
    assert [r["galpon"] for r in registros] == ["Plc1_HouseB"]


def test_un_galpon_roto_no_pierde_los_otros(tmp_path, monkeypatch):
    nave(tmp_path, "MANBD_Plc1_HouseA_7", {"MANPRODUCTION_TODAYEGG.csv": None})
    nave(tmp_path, "MANBD_Plc1_HouseB_7", {"MANPRODUCTION_TODAYEGG.csv": None})
    original = bd_parser.procesar_nave

    def falla_en_A(ruta_nave, *a, **k):
        if "HouseA" in ruta_nave:
            raise RuntimeError("permiso denegado en el montaje")
        return original(ruta_nave, *a, **k)

    monkeypatch.setattr(bd_parser, "procesar_nave", falla_en_A)
    registros, _ = bd_parser.escanear(str(tmp_path), "g", "UTC")
    assert [r["galpon"] for r in registros] == ["Plc1_HouseB"]


def test_centinela_avisa_de_archivos_sin_mapear(tmp_path):
    nave(tmp_path, archivos={"MANPRODUCTION_TODAYEGG.csv": None,
                             "MANPRODUCTION_ALGONUEVO.csv": None})
    _registros, avisos = bd_parser.escanear(str(tmp_path), "g", "UTC")
    assert avisos["MANBD_Plc1_HouseA_7"] == ["MANPRODUCTION_ALGONUEVO.csv"]


def test_carpeta_inexistente_devuelve_vacio(tmp_path):
    assert bd_parser.escanear(str(tmp_path / "no-existe"), "g", "UTC") == ([], {})


def test_carpeta_que_no_matchea_se_ignora(tmp_path):
    nave(tmp_path, "CarpetaCualquiera", {"MANPRODUCTION_TODAYEGG.csv": None})
    assert bd_parser.escanear(str(tmp_path), "g", "UTC") == ([], {})
