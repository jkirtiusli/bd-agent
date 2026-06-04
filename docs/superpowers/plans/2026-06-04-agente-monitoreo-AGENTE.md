# Agente: autonomía + heartbeat (lado Agente) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el Agente reporte al Core el resultado de cada corrida (heartbeat best-effort) y pueda correr desatendido en Windows (tarea programada 12:30 PM + on-demand por `.bat`).

**Architecture:** Un módulo nuevo `bd_agent/salud.py` con `reportar(...)` que hace un POST best-effort a `{core}/v1/agente/heartbeat` (derivando la URL del `destino.url` del config). `ciclo_trabajo` pasa a devolver `(ok, registros, mensaje)` y llama a `reportar` al final. Dos `.bat` en `scripts/` para correr y para instalar la tarea programada. El heartbeat nunca rompe la corrida: la entrega de datos es lo prioritario.

**Tech Stack:** Python 3 (stdlib `urllib`), pyyaml, pytest (se agrega).

**Spec:** `docs/superpowers/specs/2026-06-04-agente-autonomia-monitoreo-design.md` (en el repo bd-core; este plan implementa la mitad "Agente").

**Repo:** bd-agent, `/Users/jkirtio/Desktop/proyectos/Kutan Tech/bd_agent_repo`.

---

## File Structure

- **Create** `bd_agent/salud.py` — `reportar(cfg, ok, registros, mensaje)`: heartbeat HTTP best-effort.
- **Modify** `bd_agent/agente.py` — `ciclo_trabajo` devuelve `(ok, registros, mensaje)` y llama a `salud.reportar`.
- **Create** `scripts/correr_agente.bat` — corre el Agente con `--once` (on-demand + lo usa la tarea).
- **Create** `scripts/instalar_tarea.bat` — crea la Tarea Programada (12:30 PM) con `schtasks`.
- **Modify** `requirements.txt` — agregar `pytest`.
- **Create** `tests/__init__.py`, `tests/test_salud.py` — bootstrap de tests + tests de salud.
- **Modify** `bd_agent/config_ejemplo.yaml` — comentar que `destino.url` se usa para derivar el heartbeat.

Contexto del código actual (no cambia su contrato salvo lo indicado):
- `agente.py`: `ciclo_trabajo(cfg, log)` hace `bd_parser.escanear(...) -> (registros, avisos)`, luego `bd_destino.entregar(registros, cfg["destino"]) -> str (mensaje)`. `main()` llama a `ciclo_trabajo` con `--once` o en loop.
- `destino.py`: `entregar(registros, cfg_destino)`; modo `http` postea a `cfg_destino["url"]` con `Authorization: Bearer <token>`.
- `config_ejemplo.yaml`: `destino: {modo, ruta_salida, url, token}`; `granja`, `zona_horaria`.

---

### Task 1: Bootstrap de pytest

**Files:**
- Modify: `requirements.txt`
- Create: `tests/__init__.py`, `tests/test_salud.py` (placeholder inicial)

- [ ] **Step 1: Agregar pytest a requirements**

`requirements.txt` actual:
```
pyyaml>=6.0
pandas>=2.0
```
Añadir al final:
```
pytest>=8.0
```

- [ ] **Step 2: Instalar**

Run: `pip3 install -r requirements.txt`
Expected: instala pytest (pyyaml/pandas ya estaban) sin error.

- [ ] **Step 3: Crear el paquete de tests con un test trivial**

Crear `tests/__init__.py` (vacío).

Crear `tests/test_salud.py`:
```python
def test_pytest_corre():
    assert True
```

- [ ] **Step 4: Correr**

Run: `python3 -m pytest tests/ -v`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add requirements.txt tests/__init__.py tests/test_salud.py
git commit -m "chore: bootstrap pytest en el Agente"
```
(terminar con línea en blanco y luego:
`Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`)

---

### Task 2: `salud.reportar` — derivar URL y armar el body

`reportar` debe: (a) si `destino.modo != "http"`, no hacer nada (no-op); (b) derivar la URL del heartbeat reemplazando el sufijo `/ingest` de `destino.url` por `/v1/agente/heartbeat`; (c) postear el body con el token; (d) atrapar cualquier excepción (best-effort) y loguear, sin propagar.

Esta task cubre la construcción de URL y body (sin red real todavía; el POST se prueba con un doble en la Task 3). Se factoriza una función pura `_url_heartbeat(url_ingest)` y `_construir_body(cfg, ok, registros, mensaje)`.

**Files:**
- Create: `bd_agent/salud.py`
- Test: `tests/test_salud.py` (reemplazar el placeholder)

- [ ] **Step 1: Escribir el test**

Reemplazar el contenido de `tests/test_salud.py` por:
```python
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
```

- [ ] **Step 2: Correr para ver fallar**

Run: `python3 -m pytest tests/test_salud.py -v`
Expected: FAIL con `ModuleNotFoundError: bd_agent.salud`.

- [ ] **Step 3: Implementar**

Crear `bd_agent/salud.py`:
```python
# -*- coding: utf-8 -*-
"""Heartbeat best-effort del Agente al Core. Nunca rompe la corrida."""
import json
import logging
import datetime as dt
import urllib.request

log = logging.getLogger("agente.salud")


def _url_heartbeat(url_ingest):
    """Deriva la URL del heartbeat desde la URL de /ingest del config."""
    u = url_ingest.rstrip("/")
    if u.endswith("/ingest"):
        base = u[: -len("/ingest")]
    else:
        base = u
    return base + "/v1/agente/heartbeat"


def _construir_body(cfg, ok, registros, mensaje):
    return {
        "granja": cfg.get("granja"),
        "ok": bool(ok),
        "registros": int(registros or 0),
        "mensaje": mensaje,
        "agente_ts": dt.datetime.now().astimezone().isoformat(),
    }


def reportar(cfg, ok, registros, mensaje):
    """POST best-effort a {core}/v1/agente/heartbeat. Si falla, loguea y sigue."""
    destino = cfg.get("destino", {})
    if destino.get("modo") != "http":
        return  # en modo local_json no se reporta
    url = _url_heartbeat(destino["url"])
    body = _construir_body(cfg, ok, registros, mensaje)
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    token = destino.get("token", "")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status not in (200, 201):
                log.warning(f"[heartbeat] HTTP {resp.status} en {url}")
    except Exception as e:
        log.warning(f"[heartbeat] no se pudo reportar a {url}: {e}")
```

- [ ] **Step 4: Correr**

Run: `python3 -m pytest tests/test_salud.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add bd_agent/salud.py tests/test_salud.py
git commit -m "feat: salud.reportar — heartbeat best-effort (URL derivada + body)"
```
(con el trailer Co-Authored-By)

---

### Task 3: `reportar` es best-effort (no propaga errores de red)

**Files:**
- Test: `tests/test_salud.py` (APPEND)

- [ ] **Step 1: Escribir el test**

Append a `tests/test_salud.py`:
```python
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
```

- [ ] **Step 2: Correr para ver fallar... o pasar**

Run: `python3 -m pytest tests/test_salud.py -k "no_propaga or noop" -v`
Expected: PASS directamente (la implementación de la Task 2 ya es best-effort y no-op).
Si alguno fallara, corregir `reportar` para cumplir ambos contratos.

- [ ] **Step 3: Commit**

```bash
git add tests/test_salud.py
git commit -m "test: reportar es best-effort y no-op fuera de modo http"
```
(con el trailer Co-Authored-By)

---

### Task 4: `ciclo_trabajo` reporta heartbeat

`ciclo_trabajo` pasa a devolver `(ok, registros, mensaje)` y llama a `salud.reportar` al final, tanto en éxito como en error. `main()` no necesita usar el valor de retorno, pero el retorno habilita testearlo.

**Files:**
- Modify: `bd_agent/agente.py`
- Test: `tests/test_agente.py` (nuevo)

- [ ] **Step 1: Escribir el test**

Crear `tests/test_agente.py`:
```python
import logging


def test_ciclo_reporta_heartbeat(monkeypatch):
    import bd_agent.agente as agente

    # parser devuelve 2 registros, destino "entrega" ok
    monkeypatch.setattr(agente.bd_parser, "escanear",
                        lambda ruta, granja, zona: ([{"x": 1}, {"x": 2}], {}))
    monkeypatch.setattr(agente.bd_destino, "entregar",
                        lambda regs, dest: f"{len(regs)} enviados")
    reportes = []
    monkeypatch.setattr(agente.salud, "reportar",
                        lambda cfg, ok, registros, mensaje:
                        reportes.append((ok, registros, mensaje)))

    cfg = {"granja": "g", "zona_horaria": "UTC", "ruta_csv": "x",
           "destino": {"modo": "http", "url": "u", "token": "t"}}
    ok, n, msg = agente.ciclo_trabajo(cfg, logging.getLogger("t"))
    assert ok is True and n == 2 and "enviados" in msg
    assert reportes == [(True, 2, msg)]


def test_ciclo_reporta_error_si_entregar_falla(monkeypatch):
    import bd_agent.agente as agente
    monkeypatch.setattr(agente.bd_parser, "escanear",
                        lambda ruta, granja, zona: ([{"x": 1}], {}))

    def falla(regs, dest):
        raise RuntimeError("core caido")
    monkeypatch.setattr(agente.bd_destino, "entregar", falla)
    reportes = []
    monkeypatch.setattr(agente.salud, "reportar",
                        lambda cfg, ok, registros, mensaje:
                        reportes.append((ok, registros, mensaje)))
    cfg = {"granja": "g", "zona_horaria": "UTC", "ruta_csv": "x",
           "destino": {"modo": "http", "url": "u", "token": "t"}}
    ok, n, msg = agente.ciclo_trabajo(cfg, logging.getLogger("t"))
    assert ok is False and "core caido" in msg
    assert reportes and reportes[0][0] is False
```

- [ ] **Step 2: Correr para ver fallar**

Run: `python3 -m pytest tests/test_agente.py -v`
Expected: FAIL (`ciclo_trabajo` hoy no devuelve tupla ni importa `salud`).

- [ ] **Step 3: Implementar**

En `bd_agent/agente.py`:

(a) Agregar el import junto a los otros `from bd_agent import ...`:
```python
from bd_agent import salud
```

(b) Reemplazar la función `ciclo_trabajo` actual por:
```python
def ciclo_trabajo(cfg, log):
    """Lee CSV, entrega al destino y reporta heartbeat. Devuelve (ok, n, msg)."""
    zona = cfg.get("zona_horaria", "UTC")
    registros, avisos = bd_parser.escanear(cfg["ruta_csv"], cfg["granja"], zona)
    if not registros:
        log.warning("No se encontraron registros. Revisa 'ruta_csv'.")
        salud.reportar(cfg, ok=True, registros=0, mensaje="sin registros nuevos")
        return True, 0, "sin registros nuevos"
    if avisos and cfg.get("centinela", True):
        for nave, archs in avisos.items():
            log.info(f"[centinela] {nave}: datos nuevos sin mapear -> {', '.join(archs)}")
    try:
        resultado = bd_destino.entregar(registros, cfg["destino"])
    except Exception as e:
        msg = f"error al entregar: {e}"
        log.error(msg)
        salud.reportar(cfg, ok=False, registros=len(registros), mensaje=msg)
        return False, len(registros), msg
    log.info(resultado)
    salud.reportar(cfg, ok=True, registros=len(registros), mensaje=resultado)
    return True, len(registros), resultado
```

(c) En `main()`, el loop `while True` llama a `ciclo_trabajo(cfg, log)` y hoy no usa el retorno — dejarlo igual (ignora la tupla). El bloque `if args.once: ciclo_trabajo(cfg, log); return` también queda igual. No hace falta más cambio.

- [ ] **Step 4: Correr**

Run: `python3 -m pytest tests/ -v`
Expected: PASS (todos: salud + agente).

- [ ] **Step 5: Commit**

```bash
git add bd_agent/agente.py tests/test_agente.py
git commit -m "feat: ciclo_trabajo reporta heartbeat (ok/error) y devuelve (ok,n,msg)"
```
(con el trailer Co-Authored-By)

---

### Task 5: Scripts de Windows (correr + instalar tarea)

No hay test automatizado (son `.bat` de Windows). Se validan manualmente en la PC.

**Files:**
- Create: `scripts/correr_agente.bat`
- Create: `scripts/instalar_tarea.bat`

- [ ] **Step 1: Crear `scripts/correr_agente.bat`**

```bat
@echo off
REM Corre el Agente una vez (--once). Sirve para on-demand (doble clic)
REM y lo invoca la Tarea Programada. Ajustar AGENTE_DIR si el repo no esta
REM en C:\farmapi\agente.
setlocal
set AGENTE_DIR=C:\farmapi\agente
cd /d "%AGENTE_DIR%"

REM Activar venv si existe (opcional)
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"

echo [%date% %time%] Corriendo agente --once >> agente.log
python -m bd_agent.agente --config config.yaml --once >> agente.log 2>&1
echo [%date% %time%] Fin (exit %errorlevel%) >> agente.log
endlocal
```

- [ ] **Step 2: Crear `scripts/instalar_tarea.bat`**

```bat
@echo off
REM Crea la Tarea Programada que corre el Agente 1 vez por dia a las 12:30 PM.
REM Ejecutar este .bat como Administrador (clic derecho -> Ejecutar como administrador).
REM Ajustar AGENTE_DIR y HORA si hace falta.
setlocal
set AGENTE_DIR=C:\farmapi\agente
set HORA=12:30
set NOMBRE_TAREA=AgenteBDCopy

schtasks /Create /TN "%NOMBRE_TAREA%" ^
  /TR "\"%AGENTE_DIR%\scripts\correr_agente.bat\"" ^
  /SC DAILY /ST %HORA% /F

echo.
echo Tarea "%NOMBRE_TAREA%" creada: corre diariamente a las %HORA%.
echo.
echo NOTA: por defecto la tarea solo corre si el usuario esta logueado.
echo Para que corra este o no conectado el usuario, recrear con credenciales:
echo   schtasks /Create /TN "%NOMBRE_TAREA%" /TR "..." /SC DAILY /ST %HORA% /RU usuario /RP password /F
echo.
echo Para probar ahora:   schtasks /Run /TN "%NOMBRE_TAREA%"
echo Para ver estado:     schtasks /Query /TN "%NOMBRE_TAREA%" /V /FO LIST
echo Para borrarla:       schtasks /Delete /TN "%NOMBRE_TAREA%" /F
endlocal
```

- [ ] **Step 3: Commit**

```bash
git add scripts/correr_agente.bat scripts/instalar_tarea.bat
git commit -m "feat: scripts Windows — correr_agente.bat + instalar_tarea.bat (12:30 PM)"
```
(con el trailer Co-Authored-By)

---

### Task 6: Documentar el heartbeat en el config de ejemplo

**Files:**
- Modify: `bd_agent/config_ejemplo.yaml`

- [ ] **Step 1: Editar el bloque `destino` del config de ejemplo**

El bloque `destino` actual termina así:
```yaml
destino:
  modo: "local_json"       # local_json | http
  ruta_salida: 'C:\farmapi\datos_normalizados.json'
  url: "https://tu-servidor.com/ingest"
  token: "PEGAR_TOKEN"
```
Reemplazarlo por (agrega comentarios; no cambia las claves):
```yaml
destino:
  modo: "http"             # local_json | http  (produccion: http)
  ruta_salida: 'C:\farmapi\datos_normalizados.json'
  # url del /ingest del Core. El heartbeat se deriva de aca:
  #   .../ingest  ->  .../v1/agente/heartbeat
  url: "https://core.flowkore.com/ingest"
  token: "PEGAR_TOKEN"     # CORE_INGEST_TOKEN (el mismo del Core)
```

- [ ] **Step 2: Commit**

```bash
git add bd_agent/config_ejemplo.yaml
git commit -m "docs: config de ejemplo apunta al Core y documenta el heartbeat"
```
(con el trailer Co-Authored-By)

---

## Notas de cierre (Agente)

- El `config.yaml` REAL de la granja (no versionado) debe quedar con `modo: "http"`,
  `url: https://core.flowkore.com/ingest` y el `token` correcto. El plan no lo toca
  (no está en el repo); se ajusta en la PC.
- Tras desplegar el Core (plan CORE), validar end-to-end: correr `scripts\correr_agente.bat`
  en la PC y luego `curl -s https://core.flowkore.com/v1/agente/estado` desde afuera →
  debe aparecer la granja en `verde`.
- La tarea programada (12:30 PM) se instala una vez con `instalar_tarea.bat` (como Admin).
