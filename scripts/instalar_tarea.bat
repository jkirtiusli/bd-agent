@echo off
REM Crea la Tarea Programada que corre el Agente 1 vez por dia a las 12:30 PM.
REM Ejecutar este .bat como Administrador (clic derecho -> Ejecutar como administrador).
REM Ajustar HORA si hace falta. La ruta del correr_agente.bat se deduce sola
REM (esta junto a este .bat), asi que no hay que editar rutas.
setlocal
set CORRER_BAT=%~dp0correr_agente.bat
set HORA=12:30
set NOMBRE_TAREA=AgenteBDCopy

schtasks /Create /TN "%NOMBRE_TAREA%" ^
  /TR "\"%CORRER_BAT%\"" ^
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
