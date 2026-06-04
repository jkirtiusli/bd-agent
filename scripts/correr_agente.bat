@echo off
REM Corre el Agente una vez (--once). Sirve para on-demand (doble clic)
REM y lo invoca la Tarea Programada.
REM AGENTE_DIR se deduce solo: es la carpeta padre de este .bat (raiz del
REM repo, la que contiene la carpeta bd_agent\). Asi funciona en cualquier
REM PC sin editar la ruta, este el repo donde este.
setlocal
set AGENTE_DIR=%~dp0..
cd /d "%AGENTE_DIR%"

REM Interactivo (doble clic) vs desatendido (tarea programada).
REM La tarea programada invoca este .bat con el argumento "--tarea" (ver
REM instalar_tarea.bat). Sin ese argumento = doble clic = interactivo:
REM mostramos en pantalla y pausamos al final para que la ventana no se
REM cierre. Con "--tarea" = todo al log, sin pausa.
set INTERACTIVO=1
if /i "%~1"=="--tarea" set INTERACTIVO=

REM Activar venv si existe (opcional)
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"

if defined INTERACTIVO (
  REM Doble clic: la salida va a la pantalla, asi ves el progreso en vivo
  REM y un error no se pierde aunque el log este bloqueado por otra app.
  echo [%date% %time%] Corriendo agente --once
  python -m bd_agent.agente --config config.yaml --once
  echo [%date% %time%] Fin (exit %errorlevel%)
  echo.
  echo ===== Termino. Revisa arriba el resultado. Apreta una tecla para cerrar. =====
  pause >nul
) else (
  REM Tarea programada (desatendido): todo al log, sin pausa.
  echo [%date% %time%] Corriendo agente --once >> agente.log
  python -m bd_agent.agente --config config.yaml --once >> agente.log 2>&1
  echo [%date% %time%] Fin (exit %errorlevel%) >> agente.log
)
endlocal
