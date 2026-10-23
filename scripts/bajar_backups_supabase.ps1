# Baja a esta PC las copias del backup de Supabase que el VPS deja todos los
# dias en el Storage Box de Hetzner (core_v1: deploy/vps-scripts/backup-storagebox.sh,
# cron 04:00 UTC). Es una segunda copia fuera de Hetzner.
#
# Del lado remoto es SOLO LECTURA: entra con una subcuenta Read-Only del
# Storage Box limitada a supabase-backups, con una clave SSH propia de esta PC.
# Los archivos llegan y quedan cifrados con GPG: la passphrase NO esta en esta
# PC, asi que quien tenga acceso a la PC ve archivos .gpg que no puede abrir.
#
# Cada copia baja primero a _parcial y recien se mueve a su carpeta con fecha
# cuando todos los archivos tienen el tamano que reporta el Storage Box. Una
# carpeta con fecha en el destino es siempre una copia completa.
#
# Codigo de salida: 0 ok | 1 fallo (se ve en "Ultimo resultado" de la tarea).
param(
    [string]$Usuario  = "u607407-sub1",
    [string]$Servidor = "u607407-sub1.your-storagebox.de",
    [string]$Destino  = "C:\backups-supabase",
    [string]$Clave    = "C:\ProgramData\backup-supabase\id_ed25519",
    [int]   $Dias     = 30
)

# "Continue" y no "Stop": en PowerShell 5 con Stop, cualquier linea que un
# ejecutable nativo (ssh/sftp) escriba en stderr corta el script. Los errores
# se miran por $LASTEXITCODE.
$ErrorActionPreference = "Continue"

$conf     = Split-Path $Clave
$hosts    = Join-Path $conf "known_hosts"
$lote     = Join-Path $conf "lote-sftp.txt"
$log      = Join-Path $Destino "backup.log"
$parcial  = Join-Path $Destino "_parcial"
$remoto   = "$Usuario@$Servidor"
# El Storage Box habla SSH/SFTP por el puerto 23, no el 22.
$sshOpts  = @("-n", "-p", "23", "-i", $Clave, "-o", "UserKnownHostsFile=$hosts",
              "-o", "BatchMode=yes", "-o", "ConnectTimeout=30")
$sftpOpts = @("-P", "23", "-i", $Clave, "-o", "UserKnownHostsFile=$hosts",
              "-o", "BatchMode=yes", "-o", "ConnectTimeout=30")

New-Item -ItemType Directory $Destino -Force | Out-Null

function Log($m) {
    $linea = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m
    Add-Content $log $linea
    Write-Output $linea
}

function Remoto($comando) {
    $salida = & ssh.exe @sshOpts $remoto $comando 2>&1
    if ($LASTEXITCODE -ne 0) { throw "ssh '$comando' fallo: $salida" }
    $salida | ForEach-Object { "$_".Trim() } | Where-Object { $_ }
}

$patron = '^\d{4}-\d{2}-\d{2}_\d{6}$'

try {
    Log "=== Inicio (ultimos $Dias dias) ==="
    $corte = (Get-Date).Date.AddDays(-$Dias).ToString("yyyy-MM-dd")

    $copias = Remoto "ls" | Where-Object { $_ -match $patron -and $_.Substring(0, 10) -ge $corte } |
              Sort-Object
    if (-not $copias) { throw "el Storage Box no lista ninguna copia desde $corte" }

    $nuevas = 0
    foreach ($copia in $copias) {
        $final = Join-Path $Destino $copia
        if (Test-Path $final) { continue }

        # Tamano de cada archivo segun el Storage Box (ls -l: el 5to campo).
        $esperado = @{}
        foreach ($l in (Remoto "ls -l $copia")) {
            $c = $l -split '\s+'
            if ($c.Count -ge 9 -and $c[0].StartsWith("-")) { $esperado[$c[-1]] = [int64]$c[4] }
        }
        if ($esperado.Count -eq 0) { throw "$copia esta vacia en el Storage Box" }

        Remove-Item $parcial -Recurse -Force -ErrorAction SilentlyContinue
        New-Item -ItemType Directory $parcial | Out-Null
        Set-Content $lote @("lcd `"$parcial`"", "get $copia/*") -Encoding ASCII
        $salida = & sftp.exe @sftpOpts -b $lote $remoto 2>&1
        if ($LASTEXITCODE -ne 0) { throw "sftp $copia fallo: $salida" }

        foreach ($nombre in $esperado.Keys) {
            $local = Join-Path $parcial $nombre
            $tam = if (Test-Path $local) { (Get-Item $local).Length } else { -1 }
            if ($tam -ne $esperado[$nombre]) {
                throw "$copia/$nombre incompleto: $tam de $($esperado[$nombre]) bytes"
            }
        }
        Move-Item $parcial $final
        $mb = [math]::Round(($esperado.Values | Measure-Object -Sum).Sum / 1MB, 1)
        Log "  bajada $copia ($($esperado.Count) archivos, $mb MB)"
        $nuevas++
    }

    # Retencion local: solo carpetas con formato de fecha, nunca otra cosa.
    Get-ChildItem $Destino -Directory | Where-Object {
        $_.Name -match $patron -and $_.Name.Substring(0, 10) -lt $corte
    } | ForEach-Object {
        Remove-Item $_.FullName -Recurse -Force
        Log "  borrada por antigua: $($_.Name)"
    }

    $total = (Get-ChildItem $Destino -Directory | Where-Object { $_.Name -match $patron }).Count
    Log "=== OK: $nuevas nuevas, $total copias en $Destino ==="
    exit 0
}
catch {
    Log "FALLO: $_"
    exit 1
}
finally {
    Remove-Item $lote -ErrorAction SilentlyContinue
}
