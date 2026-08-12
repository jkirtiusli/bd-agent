# -*- coding: utf-8 -*-
"""
Eleccion y validacion de la carpeta de BD-Copy.

La ruta mal configurada es el error mas caro del sistema: el Agente no falla,
simplemente no encuentra nada. Por eso validar significa parsear de verdad.
"""
import datetime as dt

import pytest

from bd_agent import origen as bd_origen


CABECERA = "DATE\tTIME\tNUM\tPRODDAY\tPRODWEEK"


def granja(tmp_path, naves=("MANBD_Plc1_HouseA_7",), con_datos=True, sub="csv"):
    base = tmp_path / sub
    base.mkdir(parents=True, exist_ok=True)
    ayer = dt.date.today() - dt.timedelta(days=1)
    for nave in naves:
        d = base / nave
        d.mkdir()
        contenido = CABECERA
        if con_datos:
            contenido += (f"\n{ayer.strftime('%d.%m.%Y')}\t"
                          f"{ayer.strftime('%d/%m/%Y')} 22:00\t15000\t200\t29")
        (d / "MANPRODUCTION_TODAYEGG.csv").write_text(contenido, encoding="latin-1")
    return base


# ---------------- validar ----------------

def test_carpeta_correcta(tmp_path):
    base = granja(tmp_path, ("MANBD_Plc1_HouseA_7", "MANBD_Plc1_HouseB_7"))
    inf = bd_origen.validar(str(base))
    assert inf["ok"] is True
    assert len(inf["naves"]) == 2 and inf["csv_totales"] == 2
    assert inf["registros"] == 1 and inf["metricas"] == ["huevos"]
    assert inf["fecha_mas_nueva"] == (dt.date.today() - dt.timedelta(days=1)).isoformat()


def test_carpeta_inexistente(tmp_path):
    inf = bd_origen.validar(str(tmp_path / "no-existe"))
    assert inf["ok"] is False and "no existe" in inf["motivo"]


def test_ruta_vacia(tmp_path):
    assert bd_origen.validar("")["ok"] is False
    assert bd_origen.validar(None)["ok"] is False


def test_apunta_a_un_archivo(tmp_path):
    arch = tmp_path / "algo.txt"
    arch.write_text("x", encoding="utf-8")
    inf = bd_origen.validar(str(arch))
    assert inf["ok"] is False and "no a una carpeta" in inf["motivo"]


def test_carpeta_sin_galpones(tmp_path):
    """El error tipico: eligieron la carpeta padre, o BackUp en vez de csv."""
    (tmp_path / "BackUp").mkdir()
    inf = bd_origen.validar(str(tmp_path))
    assert inf["ok"] is False and "ningun galpon" in inf["motivo"]


def test_galpones_sin_csv(tmp_path):
    base = tmp_path / "csv"
    (base / "MANBD_Plc1_HouseA_7").mkdir(parents=True)
    inf = bd_origen.validar(str(base))
    assert inf["ok"] is False and "ningun archivo .csv" in inf["motivo"].replace(
        "ninguno tiene archivos .csv", "ningun archivo .csv")


def test_galpones_sin_datos_cerrados(tmp_path):
    """Carpeta recien creada: existe, tiene CSV, pero no hay dia cerrado."""
    base = granja(tmp_path, con_datos=False)
    inf = bd_origen.validar(str(base))
    assert inf["ok"] is False and "no tiene ningun dato" in inf["motivo"]


def test_describir_no_explota(tmp_path):
    lineas = []
    bd_origen.describir(bd_origen.validar(str(granja(tmp_path))), lineas.append)
    assert any("Carpeta valida" in l for l in lineas)
    lineas.clear()
    bd_origen.describir(bd_origen.validar("/no/existe"), lineas.append)
    assert any("NO sirve" in l for l in lineas)


# ---------------- autodeteccion ----------------

def test_encuentra_la_carpeta(tmp_path):
    base = granja(tmp_path, sub="Escritorio/BDCopy/BackUp/csv")
    assert bd_origen.buscar_candidatas([str(tmp_path)]) == [str(base)]


def test_no_baja_dentro_del_galpon(tmp_path):
    """Encontrado el nivel de los galpones, no tiene sentido seguir bajando."""
    granja(tmp_path, sub="csv")
    (tmp_path / "csv" / "MANBD_Plc1_HouseA_7" / "sub" / "MANBD_Plc9_HouseZ_1").mkdir(parents=True)
    assert bd_origen.buscar_candidatas([str(tmp_path)]) == [str(tmp_path / "csv")]


def test_respeta_el_limite_de_profundidad(tmp_path):
    granja(tmp_path, sub="a/b/c/d/e/f/g/csv")
    assert bd_origen.buscar_candidatas([str(tmp_path)], max_prof=3) == []


def test_respeta_el_limite_de_directorios(tmp_path):
    granja(tmp_path, sub="a/b/csv")
    assert bd_origen.buscar_candidatas([str(tmp_path)], max_dirs=1) == []


def test_sin_candidatas(tmp_path):
    (tmp_path / "vacio").mkdir()
    assert bd_origen.buscar_candidatas([str(tmp_path)]) == []


# ---------------- guardar en el config ----------------

def test_guardar_preserva_comentarios(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("# comentario importante\ngranja: \"g\"\nruta_csv: 'C:\\vieja'\n"
                   "intervalo_segundos: 900\n", encoding="utf-8")
    bd_origen.guardar_ruta_csv(str(cfg), r"C:\nueva\csv")
    texto = cfg.read_text(encoding="utf-8")
    assert "# comentario importante" in texto
    assert "intervalo_segundos: 900" in texto
    assert r"ruta_csv: 'C:\nueva\csv'" in texto
    assert "C:\\vieja" not in texto


def test_guardar_ruta_de_windows_no_se_escapa(tmp_path):
    """Comilla simple de YAML: la barra invertida no se interpreta."""
    import yaml
    cfg = tmp_path / "config.yaml"
    cfg.write_text("ruta_csv: 'x'\n", encoding="utf-8")
    ruta = r"C:\Users\PC\OneDrive\Escritorio\BDCopy\BackUp\csv"
    bd_origen.guardar_ruta_csv(str(cfg), ruta)
    assert yaml.safe_load(cfg.read_text(encoding="utf-8"))["ruta_csv"] == ruta


def test_guardar_ruta_con_apostrofe(tmp_path):
    import yaml
    cfg = tmp_path / "config.yaml"
    cfg.write_text("ruta_csv: 'x'\n", encoding="utf-8")
    ruta = r"C:\Datos\Granja d'Oro\csv"
    bd_origen.guardar_ruta_csv(str(cfg), ruta)
    assert yaml.safe_load(cfg.read_text(encoding="utf-8"))["ruta_csv"] == ruta


def test_guardar_crea_el_config_desde_la_plantilla(tmp_path):
    import yaml
    cfg = tmp_path / "nuevo" / "config.yaml"
    bd_origen.guardar_ruta_csv(str(cfg), r"C:\csv")
    datos = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert datos["ruta_csv"] == r"C:\csv"
    assert datos["granja"] and datos["destino"]["url"]  # vino de la plantilla


def test_guardar_agrega_la_clave_si_falta(tmp_path):
    import yaml
    cfg = tmp_path / "config.yaml"
    cfg.write_text("granja: g\n", encoding="utf-8")
    bd_origen.guardar_ruta_csv(str(cfg), "/mnt/csv")
    assert yaml.safe_load(cfg.read_text(encoding="utf-8"))["ruta_csv"] == "/mnt/csv"


def test_leer_ruta_actual(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("ruta_csv: 'C:\\datos\\csv'\n", encoding="utf-8")
    assert bd_origen._ruta_actual(str(cfg)) == r"C:\datos\csv"
    assert bd_origen._ruta_actual(str(tmp_path / "no.yaml")) is None


# ---------------- asistente ----------------

@pytest.fixture
def respuestas():
    def hacer(*valores):
        it = iter(valores)
        return lambda _prompt="": next(it)
    return hacer


def test_asistente_elige_una_candidata(tmp_path, respuestas):
    import yaml
    base = granja(tmp_path, sub="csv")
    cfg = tmp_path / "config.yaml"
    salida = []
    codigo = bd_origen.configurar_interactivo(
        str(cfg), imprimir=salida.append, preguntar=respuestas("1"),
        buscar=lambda: [str(base)])
    assert codigo == 0
    assert yaml.safe_load(cfg.read_text(encoding="utf-8"))["ruta_csv"] == str(base)


def test_asistente_usa_el_explorador(tmp_path, respuestas):
    import yaml
    base = granja(tmp_path, sub="csv")
    cfg = tmp_path / "config.yaml"
    codigo = bd_origen.configurar_interactivo(
        str(cfg), imprimir=lambda *_: None, preguntar=respuestas("B"),
        buscar=lambda: [], elegir=lambda inicial=None: str(base))
    assert codigo == 0
    assert yaml.safe_load(cfg.read_text(encoding="utf-8"))["ruta_csv"] == str(base)


def test_asistente_rechaza_carpeta_mala_y_reintenta(tmp_path, respuestas):
    """Elige una carpeta sin galpones; el asistente la rechaza y pide otra."""
    import yaml
    base = granja(tmp_path, sub="csv")
    mala = tmp_path / "mala"
    mala.mkdir()
    cfg = tmp_path / "config.yaml"
    salida = []
    codigo = bd_origen.configurar_interactivo(
        str(cfg), imprimir=salida.append,
        preguntar=respuestas("E", str(mala), "E", str(base)), buscar=lambda: [])
    assert codigo == 0
    assert any("no tiene ningun galpon" in l for l in salida)
    assert yaml.safe_load(cfg.read_text(encoding="utf-8"))["ruta_csv"] == str(base)


def test_asistente_se_puede_cancelar(tmp_path, respuestas):
    cfg = tmp_path / "config.yaml"
    codigo = bd_origen.configurar_interactivo(
        str(cfg), imprimir=lambda *_: None, preguntar=respuestas("X"),
        buscar=lambda: [])
    assert codigo == 1 and not cfg.exists()


def test_asistente_confirma_la_ruta_que_ya_estaba(tmp_path, respuestas):
    base = granja(tmp_path, sub="csv")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"ruta_csv: '{base}'\n", encoding="utf-8")
    llamadas = []
    codigo = bd_origen.configurar_interactivo(
        str(cfg), imprimir=lambda *_: None, preguntar=respuestas(""),
        buscar=lambda: llamadas.append(1) or [])
    assert codigo == 0 and llamadas == []  # ni siquiera hizo falta buscar
