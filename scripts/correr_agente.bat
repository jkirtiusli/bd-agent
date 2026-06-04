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
