# -*- coding: utf-8 -*-
"""
`--explorar`: que datos hay disponibles ademas de los 12 mapeados.

Es la herramienta para contestar "¿podemos sacar temperatura?" sin tener que
entrar a la granja a mirar carpetas.
"""
import datetime as dt

import pytest

from bd_agent import explorador as expl


AYER = dt.date.today() - dt.timedelta(days=1)
F = AYER.strftime("%d.%m.%Y")


def csv_produccion(valor=15000):
    return (f"DATE\tTIME\tNUM\tPRODDAY\tPRODWEEK\n"
            f"{F}\t{F} 22:00\t{valor}\t200\t29")


def nave(tmp_path, nombre, archivos):
    d = tmp_path / nombre
    d.mkdir(parents=True, exist_ok=True)
    for arch, contenido in archivos.items():
        (d / arch).write_text(contenido, encoding="latin-1")
    return d


# ---------------- inspeccion de un archivo ----------------

def test_inspecciona_un_csv_normal(tmp_path):
    d = nave(tmp_path, "MANBD_Plc1_HouseA_7", {"X.csv": csv_produccion()})
    info = expl.inspeccionar_archivo(str(d / "X.csv"))
    assert info["columnas"] == ["DATE", "TIME", "NUM", "PRODDAY", "PRODWEEK"]
    assert info["filas"] == 1 and info["filas_con_fecha"] == 1
    assert info["desde"] == AYER and info["hasta"] == AYER
    assert info["numericas"]["NUM"] == "15000"
    assert "TIME" not in info["numericas"]  # no es un valor de metrica


def test_archivo_vacio_no_explota(tmp_path):
    d = nave(tmp_path, "MANBD_Plc1_HouseA_7", {"X.csv": ""})
    assert expl.inspeccionar_archivo(str(d / "X.csv"))["error"] == "archivo vacio"


def test_detecta_columnas_distintas(tmp_path):
    """Un archivo de clima puede no tener NUM: eso hay que verlo."""
    d = nave(tmp_path, "MANBD_Plc1_HouseA_7",
             {"CLIMA.csv": f"DATE\tTIME\tMIN\tMAX\tAVG\n{F}\t{F} 22:00\t18.5\t27.2\t22.4"})
    info = expl.inspeccionar_archivo(str(d / "CLIMA.csv"))
    assert info["columnas"] == ["DATE", "TIME", "MIN", "MAX", "AVG"]
    assert set(info["numericas"]) == {"MIN", "MAX", "AVG"}
    assert info["numericas"]["MAX"] == "27.2"


# ---------------- exploracion completa ----------------

def test_separa_mapeados_de_nuevos(tmp_path):
    for casa in ("MANBD_Plc1_HouseA_7", "MANBD_Plc1_HouseB_7"):
        nave(tmp_path, casa, {
            "MANPRODUCTION_TODAYEGG.csv": csv_produccion(),
            "MANCLIMATE_TEMPERATURA.csv": csv_produccion(24),
        })
    informe = expl.explorar(str(tmp_path))
    assert len(informe["naves"]) == 2

    egg = informe["archivos"]["MANPRODUCTION_TODAYEGG.csv"]
    assert egg["mapeado"] is True and egg["metrica"] == "huevos"
    assert egg["en_naves"] == 2 and egg["con_datos"] == 2

    temp = informe["archivos"]["MANCLIMATE_TEMPERATURA.csv"]
    assert temp["mapeado"] is False and temp["metrica"] is None
    assert temp["filas"] == 2  # una fila por galpon
    assert temp["numericas"]["NUM"] == "24"


def test_acumula_el_rango_de_fechas_entre_naves(tmp_path):
    viejo = (dt.date.today() - dt.timedelta(days=30)).strftime("%d.%m.%Y")
    nave(tmp_path, "MANBD_Plc1_HouseA_7",
         {"NUEVO.csv": f"DATE\tTIME\tNUM\n{viejo}\tx\t1"})
    nave(tmp_path, "MANBD_Plc1_HouseB_7",
         {"NUEVO.csv": f"DATE\tTIME\tNUM\n{F}\tx\t2"})
    a = expl.explorar(str(tmp_path))["archivos"]["NUEVO.csv"]
    assert a["desde"] == dt.date.today() - dt.timedelta(days=30)
    assert a["hasta"] == AYER


def test_sin_datos_no_cuenta_como_nuevo(tmp_path):
    nave(tmp_path, "MANBD_Plc1_HouseA_7", {"VACIO.csv": "DATE\tTIME\tNUM\n"})
    a = expl.explorar(str(tmp_path))["archivos"]["VACIO.csv"]
    assert a["filas"] == 0 and a["con_datos"] == 0


def test_ignora_carpetas_que_no_son_galpon(tmp_path):
    nave(tmp_path, "OtraCosa", {"X.csv": csv_produccion()})
    informe = expl.explorar(str(tmp_path))
    assert informe["naves"] == [] and informe["archivos"] == {}


def test_carpeta_inexistente(tmp_path):
    assert expl.explorar(str(tmp_path / "no-existe"))["naves"] == []


# ---------------- informe ----------------

@pytest.fixture
def lineas():
    salida = []
    return salida, salida.append


def test_informe_destaca_lo_que_se_puede_agregar(tmp_path, lineas):
    salida, escribir = lineas
    nave(tmp_path, "MANBD_Plc1_HouseA_7", {
        "MANPRODUCTION_TODAYEGG.csv": csv_produccion(),
        "MANCLIMATE_TEMPERATURA.csv": csv_produccion(24),
    })
    expl.imprimir(expl.explorar(str(tmp_path)), escribir)
    texto = "\n".join(salida)
    assert "SIN MAPEAR, CON DATOS (1)" in texto
    assert "MANCLIMATE_TEMPERATURA.csv" in texto
    assert "huevos" in texto  # el mapeado aparece con su metrica


def test_informe_avisa_si_falta_la_columna_NUM(tmp_path, lineas):
    """Sin NUM no alcanza con mapearlo: hay que extender el parser."""
    salida, escribir = lineas
    nave(tmp_path, "MANBD_Plc1_HouseA_7",
         {"CLIMA.csv": f"DATE\tTIME\tMIN\tMAX\n{F}\tx\t18\t27"})
    expl.imprimir(expl.explorar(str(tmp_path)), escribir)
    texto = "\n".join(salida)
    assert "no tiene columna NUM" in texto
    assert "extender el parser" in texto


def test_informe_sin_galpones(tmp_path, lineas):
    salida, escribir = lineas
    expl.imprimir(expl.explorar(str(tmp_path)), escribir)
    assert any("ningun galpon" in l for l in salida)


def test_informe_cuando_ya_esta_todo_mapeado(tmp_path, lineas):
    salida, escribir = lineas
    nave(tmp_path, "MANBD_Plc1_HouseA_7",
         {"MANPRODUCTION_TODAYEGG.csv": csv_produccion()})
    expl.imprimir(expl.explorar(str(tmp_path)), escribir)
    assert any("ya esta levantando todo" in l for l in salida)
