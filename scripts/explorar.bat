@echo off
REM Doble clic para ver QUE datos hay en los CSV de BD-Copy: los que el agente
REM ya levanta y, sobre todo, los que estan disponibles y todavia no se mapean
REM (temperatura, humedad, lo que sea que exporte esta granja).
REM No manda nada al Core: es solo mirar.
setlocal EnableDelayedExpansion
cd /d "%~dp0.."

if exist "agente-bdcopy.exe" (
  agente-bdcopy.exe --config config.yaml --explorar
) else (
  if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"
  python -m bd_agent.agente --config config.yaml --explorar
)
set CODIGO=!errorlevel!
echo.
echo ===== Termino (codigo !CODIGO!). Apreta una tecla para cerrar. =====
pause >nul
endlocal & exit /b %CODIGO%
