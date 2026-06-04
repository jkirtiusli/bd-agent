@echo off
REM Corre el Agente una vez (--once). Sirve para on-demand (doble clic)
REM y lo invoca la Tarea Programada.
REM AGENTE_DIR se deduce solo: es la carpeta padre de este .bat (raiz del
REM repo, la que contiene la carpeta bd_agent\). Asi funciona en cualquier
REM PC sin editar la ruta, este el repo donde este.
setlocal
set AGENTE_DIR=%~dp0..
cd /d "%AGENTE_DIR%"

REM Activar venv si existe (opcional)
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"

echo [%date% %time%] Corriendo agente --once >> agente.log
python -m bd_agent.agente --config config.yaml --once >> agente.log 2>&1
echo [%date% %time%] Fin (exit %errorlevel%) >> agente.log
endlocal
