# Crea la Tarea Programada del Agente en Windows.
#
# Reemplaza al "schtasks /SC DAILY /ST 12:30" anterior, que tenia dos agujeros:
#   - un solo disparo por dia: si la PC estaba apagada a esa hora, se perdia el dia
#   - solo corria con el usuario logueado: tras un reinicio sin login, nada
#
# Esta version corre cada hora, como SYSTEM (no necesita usuario logueado), y
# con StartWhenAvailable: si la PC estuvo apagada, recupera la corrida perdida
# apenas enciende.
param(
    [string]$Nombre   = "AgenteBDCopy",
    [int]   $CadaMin  = 60,
    [string]$Config   = "config.yaml"
)

$ErrorActionPreference = "Stop"

$esAdmin = ([Security.Principal.WindowsPrincipal] `
            [Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $esAdmin) {
    Write-Host "ERROR: hay que correr esto como Administrador." -ForegroundColor Red
    Write-Host "Clic derecho sobre instalar_tarea.bat -> Ejecutar como administrador."
    exit 1
}

$repo   = Split-Path -Parent $PSScriptRoot
$correr = Join-Path $PSScriptRoot "correr_agente.bat"
if (-not (Test-Path $correr)) { throw "No se encontro $correr" }

Write-Host ">> Repo:   $repo"
Write-Host ">> Script: $correr"
Write-Host ">> Cada:   $CadaMin minutos"

$accion = New-ScheduledTaskAction -Execute "cmd.exe" `
    -Argument "/c `"`"$correr`" --tarea`"" -WorkingDirectory $repo

# Disparador: arranca a medianoche y se repite todo el dia. Asi una corrida
# fallida cuesta minutos, no un dia entero.
$disparador = New-ScheduledTaskTrigger -Daily -At "00:02"
$disparador.Repetition = (New-ScheduledTaskTrigger -Once -At "00:02" `
    -RepetitionInterval (New-TimeSpan -Minutes $CadaMin) `
    -RepetitionDuration (New-TimeSpan -Hours 24)).Repetition

# Segundo disparador: tambien al arrancar la PC, para no esperar a la hora.
$alArrancar = New-ScheduledTaskTrigger -AtStartup
$alArrancar.Delay = "PT3M"   # dar tiempo a que levante la red

$ajustesTarea = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)

# SYSTEM: corre este logueado o no el usuario. Es el punto que mas fallas evita.
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" `
    -LogonType ServiceAccount -RunLevel Highest

Register-ScheduledTask -TaskName $Nombre -Force `
    -Action $accion -Trigger @($disparador, $alArrancar) `
    -Settings $ajustesTarea -Principal $principal `
    -Description "Agente BD-Copy: lee los CSV, encola y entrega al Core." | Out-Null

# --- Tarea de auto-actualizacion (una vez por dia, de madrugada) ---
# Va aparte del ciclo de datos: una actualizacion que falla no puede frenar la
# entrega. Respeta actualizacion.modo del config, que arranca en "manual".
$exe = Join-Path $repo "agente-bdcopy.exe"
if (Test-Path $exe) {
    $accionAct = New-ScheduledTaskAction -Execute $exe `
        -Argument "--config `"$Config`" --actualizar --desatendido" -WorkingDirectory $repo
} else {
    $accionAct = New-ScheduledTaskAction -Execute "python" `
        -Argument "-m bd_agent.agente --config `"$Config`" --actualizar --desatendido" `
        -WorkingDirectory $repo
}
# Minuto aleatorio para que la flota no se actualice toda junta: si una version
# rompe algo, rompe de a poco y da tiempo a frenarla.
$minuto = Get-Random -Minimum 0 -Maximum 59
$dispAct = New-ScheduledTaskTrigger -Daily -At ("03:{0:d2}" -f $minuto)

Register-ScheduledTask -TaskName "$Nombre-Update" -Force `
    -Action $accionAct -Trigger $dispAct `
    -Settings $ajustesTarea -Principal $principal `
    -Description "Agente BD-Copy: busca e instala actualizaciones." | Out-Null

Write-Host ""
Write-Host "OK: tarea '$Nombre' creada." -ForegroundColor Green
Write-Host "    Corre cada $CadaMin min, como SYSTEM, y recupera corridas perdidas."
Write-Host "OK: tarea '$Nombre-Update' creada (03:$('{0:d2}' -f $minuto))." -ForegroundColor Green
Write-Host "    Solo instala si actualizacion.modo esta en 'automatica'."
Write-Host ""
Write-Host "OJO con OneDrive: si la carpeta de BD-Copy esta en OneDrive, marcarla"
Write-Host "como 'Conservar siempre en este dispositivo'. SYSTEM no puede bajar"
Write-Host "archivos que quedaron solo en la nube."
Write-Host ""
Write-Host "Probar ahora:   Start-ScheduledTask -TaskName '$Nombre'"
Write-Host "Ver estado:     Get-ScheduledTaskInfo -TaskName '$Nombre'"
Write-Host "Diagnostico:    python -m bd_agent.agente --config $Config --diagnostico"
Write-Host "Borrarla:       Unregister-ScheduledTask -TaskName '$Nombre' -Confirm:`$false"
