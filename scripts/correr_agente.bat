@echo off
REM Corre el Agente una vez (--once). Sirve para on-demand (doble clic)
REM y lo invoca la Tarea Programada.
REM AGENTE_DIR se deduce solo: es la carpeta padre de este .bat (raiz del
REM repo, la que contiene la carpeta bd_agent\). Asi funciona en cualquier
REM PC sin editar la ruta, este el repo donde este.
REM
REM Codigos de salida: 0 ok | 2 config | 3 origen (CSV) | 4 red/Core | 5 token
REM EnableDelayedExpansion: sin esto, %errorlevel% leido dentro de un bloque
REM ( ... ) devuelve el valor viejo y el codigo de salida sale siempre 0.
setlocal EnableDelayedExpansion
set AGENTE_DIR=%~dp0..
cd /d "%AGENTE_DIR%"

REM Interactivo (doble clic) vs desatendido (tarea programada).
REM La tarea programada invoca este .bat con el argumento "--tarea" (ver
REM instalar_tarea.ps1). Sin ese argumento = doble clic = interactivo:
REM mostramos en pantalla y pausamos al final para que la ventana no se
REM cierre. Con "--tarea" = todo al log, sin pausa.
set INTERACTIVO=1
if /i "%~1"=="--tarea" set INTERACTIVO=

REM Activar venv si existe (opcional)
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"

set CODIGO=0
if defined INTERACTIVO (
  REM Doble clic: la salida va a la pantalla, asi ves el progreso en vivo
  REM y un error no se pierde aunque el log este bloqueado por otra app.
  echo [%date% %time%] Corriendo agente --once
  python -m bd_agent.agente --config config.yaml --once
  set CODIGO=!errorlevel!
  echo [%date% %time%] Fin ^(exit !CODIGO!^)
  echo.
  if not "!CODIGO!"=="0" (
    echo Termino con error. Para ver que pasa:
    echo    python -m bd_agent.agente --config config.yaml --diagnostico
    echo.
  )
  echo ===== Termino. Revisa arriba el resultado. Apreta una tecla para cerrar. =====
  pause >nul
) else (
  REM Tarea programada (desatendido): todo al log, sin pausa.
  echo [%date% %time%] Corriendo agente --once >> agente.log
  python -m bd_agent.agente --config config.yaml --once >> agente.log 2>&1
  set CODIGO=!errorlevel!
  echo [%date% %time%] Fin ^(exit !CODIGO!^) >> agente.log
)
REM El codigo de salida llega a la Tarea Programada: se ve en "Ultimo resultado".
endlocal & exit /b %CODIGO%
