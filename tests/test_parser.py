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


CABECERA_CLIMA = ("DATE\tTIME\tPRODDAY\tPRODWEEK\tROOMTEMP1\tROOMTEMP2\tROOMTEMP3"
                  "\tEXTTEMP\tAIRSPEED")


def test_clima_un_registro_por_hora(tmp_path):
    ayer = dt.date.today() - dt.timedelta(days=1)
    f = ayer.strftime("%d.%m.%Y")
    nave(tmp_path, archivos={"MANPRODUCTION_AVG.csv": (
        f"{CABECERA_CLIMA}\n"
        f"{f}\t{f} 1:00\t200\t29\t20\t22\t0\t0\t1.5\n"
        f"{f}\t{f} 02:00\t200\t29\t24\t26\t0\t10\t2.5")})
    registros, avisos = bd_parser.escanear(str(tmp_path), "g", "UTC")
    temp = {r["hora"]: r for r in registros if r["metrica"] == "temperatura"}
    # cada fila del CSV es un registro; '1:00' se normaliza a '01:00'
    assert set(temp) == {"01:00", "02:00"}
    # ROOMTEMP3=0 es sonda ausente: no arrastra el promedio de la fila
    assert temp["01:00"]["valor"] == 21.0
    assert temp["02:00"]["valor"] == 25.0
    # EXTTEMP=0 si es una medicion real
    ext = {r["hora"]: r["valor"] for r in registros
           if r["metrica"] == "temperatura_exterior"}
    assert ext == {"01:00": 0.0, "02:00": 10.0}
    r = temp["01:00"]
    assert r["fecha_dato"] == ayer.isoformat()
    assert (r["edad_dia"], r["semana"], r["fuente"]) == \
        (200, 29, "MANPRODUCTION_AVG.csv")
    # sin HUMIDITY/CO2/NH3/NEGPRESSURE en la cabecera, esas metricas no se inventan
    metricas = {r["metrica"] for r in registros}
    assert "humedad" not in metricas and "co2" not in metricas
    # el archivo esta mapeado: el centinela no lo marca como nuevo
    assert avisos == {}


def test_clima_max_combina_las_sondas_de_la_fila(tmp_path):
    ayer = dt.date.today() - dt.timedelta(days=1)
    f = ayer.strftime("%d.%m.%Y")
    cabecera = "DATE\tTIME\tPRODDAY\tPRODWEEK\tROOMTEMP1\tROOMTEMP2"
    nave(tmp_path, archivos={"MANPRODUCTION_MAX.csv": (
        f"{cabecera}\n"
        f"{f}\t{f} 01:00\t200\t29\t25\t28\n"
        f"{f}\t{f} 02:00\t200\t29\t31\t27")})
    registros, _ = bd_parser.escanear(str(tmp_path), "g", "UTC")
    assert [(r["metrica"], r["hora"], r["valor"]) for r in registros] == \
        [("temperatura_max", "01:00", 28.0), ("temperatura_max", "02:00", 31.0)]


def test_clima_descarta_el_dia_en_curso(tmp_path):
    hoy = dt.date.today().strftime("%d.%m.%Y")
    nave(tmp_path, archivos={"MANPRODUCTION_AVG.csv": (
        f"{CABECERA_CLIMA}\n{hoy}\t{hoy} 01:00\t200\t29\t20\t22\t0\t5\t1.5")})
    registros, _ = bd_parser.escanear(str(tmp_path), "g", "UTC")
    assert registros == []


def test_clima_desde_limita_el_historico(tmp_path):
    ayer = dt.date.today() - dt.timedelta(days=1)
    hace_un_mes = ayer - dt.timedelta(days=30)
    filas = "".join(
        f"\n{d.strftime('%d.%m.%Y')}\t{d.strftime('%d.%m.%Y')} 01:00"
        f"\t200\t29\t20\t22\t0\t5\t1.5"
        for d in (hace_un_mes, ayer))
    nave(tmp_path, archivos={"MANPRODUCTION_AVG.csv": CABECERA_CLIMA + filas,
                             "MANPRODUCTION_TODAYEGG.csv": None})
    registros, _ = bd_parser.escanear(str(tmp_path), "g", "UTC",
                                      clima_desde=ayer)
    # el clima viejo no se encola; las metricas diarias no se ven afectadas
    fechas_clima = {r["fecha_dato"] for r in registros if r["hora"]}
    assert fechas_clima == {ayer.isoformat()}
    assert any(r["metrica"] == "huevos" for r in registros)


def test_clima_sin_hora_se_descarta(tmp_path):
    """Sin hora no hay identidad: dos filas del dia pisarian la misma clave."""
    ayer = (dt.date.today() - dt.timedelta(days=1)).strftime("%d.%m.%Y")
    cabecera = "DATE\tTIME\tPRODDAY\tPRODWEEK\tROOMTEMP1"
    nave(tmp_path, archivos={"MANPRODUCTION_AVG.csv": (
        f"{cabecera}\n{ayer}\tsin-hora\t200\t29\t20")})
    registros, _ = bd_parser.escanear(str(tmp_path), "g", "UTC")
    assert registros == []


def test_clima_sin_time_usa_la_columna_hour(tmp_path):
    ayer = (dt.date.today() - dt.timedelta(days=1)).strftime("%d.%m.%Y")
    cabecera = "DATE\tTIME\tHOUR\tPRODDAY\tPRODWEEK\tROOMTEMP1"
    nave(tmp_path, archivos={"MANPRODUCTION_AVG.csv": (
        f"{cabecera}\n{ayer}\t\t7\t200\t29\t20")})
    registros, _ = bd_parser.escanear(str(tmp_path), "g", "UTC")
    assert [(r["metrica"], r["hora"]) for r in registros] == \
        [("temperatura", "07:00")]
