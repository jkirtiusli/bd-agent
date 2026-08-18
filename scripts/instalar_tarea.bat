@echo off
REM Instala la Tarea Programada del Agente.
REM Clic derecho -> Ejecutar como administrador.
REM
REM La logica esta en instalar_tarea.ps1 (PowerShell permite configurar
REM StartWhenAvailable y la repeticion horaria, que schtasks no expone).
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar_tarea.ps1" %*
set CODIGO=%errorlevel%
echo.
echo ===== Termino (codigo %CODIGO%). Apreta una tecla para cerrar. =====
pause >nul
endlocal & exit /b %CODIGO%
