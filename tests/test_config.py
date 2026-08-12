# -*- coding: utf-8 -*-
"""Carga de config y resolucion del token fuera del YAML."""
import pytest

from bd_agent import config as bd_config
from bd_agent.config import citar_yaml


# OJO con las comillas: entre comillas DOBLES, una ruta de Windows
# (C:\Users\...) hace que YAML interprete \U como escape y falle. Por eso
# todo lo que sea ruta va con comilla simple, igual que en el config real.
BASE = """
granja: "astillas"
zona_horaria: "America/Argentina/Buenos_Aires"
ruta_csv: {csv}
destino:
  modo: "http"
  url: "https://core.flowkore.com/ingest"
{token}
"""


def escribir(tmp_path, token_yaml, csv=None):
    csv = csv or str(tmp_path)
    ruta = tmp_path / "config.yaml"
    ruta.write_text(BASE.format(csv=citar_yaml(csv), token=token_yaml), encoding="utf-8")
    return str(ruta)


def test_token_en_linea(tmp_path):
    cfg = bd_config.cargar(escribir(tmp_path, '  token: "abc123"'))
    assert cfg["destino"]["token"] == "abc123"
    assert cfg["granja"] == "astillas"


def test_token_desde_archivo(tmp_path):
    (tmp_path / "token").write_text("secreto-de-archivo\n", encoding="utf-8")
    cfg = bd_config.cargar(
        escribir(tmp_path, f'  token_file: {citar_yaml(tmp_path / "token")}'))
    assert cfg["destino"]["token"] == "secreto-de-archivo"


def test_token_file_expande_variables(tmp_path, monkeypatch):
    """Asi funciona con LoadCredential= de systemd."""
    (tmp_path / "core_token").write_text("desde-systemd", encoding="utf-8")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    cfg = bd_config.cargar(
        escribir(tmp_path, "  token_file: '${CREDENTIALS_DIRECTORY}/core_token'"))
    assert cfg["destino"]["token"] == "desde-systemd"


def test_token_desde_variable_de_entorno(tmp_path, monkeypatch):
    monkeypatch.setenv("CORE_INGEST_TOKEN", "desde-env")
    cfg = bd_config.cargar(escribir(tmp_path, '  token_env: "CORE_INGEST_TOKEN"'))
    assert cfg["destino"]["token"] == "desde-env"


def test_token_placeholder_sin_reemplazar_es_error(tmp_path):
    with pytest.raises(bd_config.ErrorConfig, match="token"):
        bd_config.cargar(escribir(tmp_path, '  token: "PEGAR_TOKEN"'))


def test_token_file_inexistente_es_error_claro(tmp_path):
    with pytest.raises(bd_config.ErrorConfig, match="token_file"):
        bd_config.cargar(escribir(tmp_path, '  token_file: "/no/existe/token"'))


def test_falta_url_en_modo_http(tmp_path):
    ruta = tmp_path / "config.yaml"
    ruta.write_text('granja: g\nruta_csv: /tmp\ndestino:\n  modo: "http"\n  token: t\n',
                    encoding="utf-8")
    with pytest.raises(bd_config.ErrorConfig, match="url"):
        bd_config.cargar(str(ruta))


def test_faltan_claves_obligatorias(tmp_path):
    ruta = tmp_path / "config.yaml"
    ruta.write_text("granja: g\n", encoding="utf-8")
    with pytest.raises(bd_config.ErrorConfig, match="ruta_csv"):
        bd_config.cargar(str(ruta))


def test_config_inexistente(tmp_path):
    with pytest.raises(bd_config.ErrorConfig, match="no existe"):
        bd_config.cargar(str(tmp_path / "nope.yaml"))


def test_spool_queda_al_lado_de_la_config(tmp_path):
    cfg = bd_config.cargar(escribir(tmp_path, '  token: "t"'))
    assert cfg["spool"]["ruta"] == str(tmp_path / "spool.db")


def test_defaults(tmp_path):
    cfg = bd_config.cargar(escribir(tmp_path, '  token: "t"'))
    d = cfg["destino"]
    assert (d["lote"], d["reintentos"], d["timeout"]) == (2000, 5, 60)


def test_ruta_de_windows_en_el_config(tmp_path):
    """
    Regresion: entre comillas DOBLES, 'C:\\Users\\...' hace que YAML lea \\U
    como escape y falle. El config real usa comilla simple; este test lo fija
    para que no vuelva a colarse (corre en cualquier sistema operativo).
    """
    ruta = tmp_path / "config.yaml"
    ruta.write_text(
        "granja: g\n"
        r"ruta_csv: 'C:\Users\PC\OneDrive\Escritorio\BDCopy\BackUp\csv'" + "\n"
        'destino:\n  modo: "http"\n  url: "https://c/ingest"\n  token: "t"\n',
        encoding="utf-8")
    cfg = bd_config.cargar(str(ruta))
    assert cfg["ruta_csv"] == r"C:\Users\PC\OneDrive\Escritorio\BDCopy\BackUp\csv"


def test_citar_yaml_rutas_de_windows():
    import yaml
    for ruta in (r"C:\Users\PC\csv", r"D:\Granja d'Oro\csv", "/mnt/bdcopy/csv"):
        assert yaml.safe_load(f"r: {bd_config.citar_yaml(ruta)}")["r"] == ruta


def test_origen_token_no_revela_el_valor(tmp_path):
    texto = bd_config.origen_token({"token_file": "/etc/bd-agent/token"})
    assert "/etc/bd-agent/token" in texto
    assert bd_config.origen_token({"token": "supersecreto"}) == "config.yaml (en linea)"
