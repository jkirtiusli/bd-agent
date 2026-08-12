@echo off
REM Doble clic para elegir la carpeta donde BD-Copy deja los CSV.
REM Abre el explorador de carpetas de Windows y verifica que los archivos se
REM puedan leer de verdad antes de guardar la ruta en config.yaml.
setlocal EnableDelayedExpansion
cd /d "%~dp0.."

if exist "agente-bdcopy.exe" (
  agente-bdcopy.exe --config config.yaml --configurar
) else (
  if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"
  python -m bd_agent.agente --config config.yaml --configurar
)
set CODIGO=!errorlevel!

echo.
echo ===== Termino (codigo !CODIGO!). Apreta una tecla para cerrar. =====
pause >nul
endlocal & exit /b %CODIGO%
