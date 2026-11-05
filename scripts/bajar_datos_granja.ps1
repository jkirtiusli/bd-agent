# Baja a esta PC el paquete diario de datos abiertos de la granja que el VPS
# deja en el Storage Box de Hetzner (core_v1: deploy/vps-scripts/paquete-granja.sh,
# cron 04:20 UTC). Cada paquete es una carpeta con fecha:
#   datos.sql.gz, csv.zip, LEEME.txt, MANIFIESTO.txt
#
# OJO: este paquete NO va cifrado. El backup cifrado completo
# (backup-storagebox.sh) queda solo en Hetzner y nunca llega a esta PC.
#
# Del lado remoto es SOLO LECTURA: subcuenta Read-Only del Storage Box cuya
# base es granja-datos, con una clave SSH propia de esta PC.
#
# Cada paquete baja primero a _parcial. Se mueve a su carpeta con fecha solo si
# MANIFIESTO.txt es valido, trae al menos datos.sql.gz, csv.zip y LEEME.txt,
# cada archivo listado tiene su tamano y su SHA256, y no sobra ningun archivo.
# Una carpeta con fecha en el destino es siempre un paquete verificado.
#
# Ping (opcional): la URL esta en ping-url.txt, junto a la clave. Se llama a
# <url> si salio bien y a <url>/fail si fallo. La URL nunca se escribe en el
# log, y un problema con el ping no cambia el codigo de salida.
#
# Codigo de salida: 0 ok | 1 fallo (se ve en "Ultimo resultado" de la tarea).
param(
    [string]$Usuario  = "u607407-sub1",
    [string]$Servidor = "u607407-sub1.your-storagebox.de",
    [string]$Destino  = "C:\datos-granja",
    [string]$Clave    = "C:\ProgramData\datos-granja\id_ed25519",
    [int]   $Copias   = 7
)

# "Continue" y no "Stop": en PowerShell 5 con Stop, cualquier linea que un
# ejecutable nativo (ssh/sftp) escriba en stderr corta el script. Los errores
# se miran por $LASTEXITCODE.
$ErrorActionPreference = "Continue"

$conf     = Split-Path $Clave
$hosts    = Join-Path $conf "known_hosts"
$lote     = Join-Path $conf "lote-sftp.txt"
$pingArch = Join-Path $conf "ping-url.txt"
$log      = Join-Path $Destino "bajada.log"
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

# $sufijo: "" si salio bien, "/fail" si fallo. Nunca escribe la URL en el log
# ni el texto de la excepcion (puede traerla).
function Ping($sufijo) {
    try {
        if (-not (Test-Path $pingArch)) { Log "ping: no hay ping-url.txt, se omite"; return }
        $url = (Get-Content $pingArch -Raw)
        if ($null -eq $url) { $url = "" }
        $url = $url.Trim().TrimEnd("/")
        if (-not $url) { Log "ping: ping-url.txt esta vacio, se omite"; return }
        [Net.ServicePointManager]::SecurityProtocol = 'Tls12'
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 15 -Uri ($url + $sufijo) | Out-Null
        Log "ping enviado$sufijo"
    }
    catch {
        Log ("ping fallo ({0}), no cambia el resultado" -f $_.Exception.GetType().Name)
    }
}

# Lee y valida MANIFIESTO.txt de $carpeta. Devuelve un hashtable nombre -> @{ Hash; Bytes }.
function LeerManifiesto($carpeta) {
    $arch = Join-Path $carpeta "MANIFIESTO.txt"
    if (-not (Test-Path -LiteralPath $arch -PathType Leaf)) { throw "falta MANIFIESTO.txt" }
    $items = @{}
    foreach ($l in (Get-Content -LiteralPath $arch)) {
        $l = "$l".TrimEnd("`r")
        if ($l -match '^([0-9a-f]{64})  (\d+)  (\S+)$') {
            $hashM = $Matches[1]; $bytesM = $Matches[2]; $nombre = $Matches[3]
        } else { continue }
        # El nombre viene de un archivo remoto y se une a una ruta local.
        if (($nombre -cnotmatch '^[A-Za-z0-9._-]+$') -or $nombre.Contains("..") -or $nombre -eq ".") {
            throw "MANIFIESTO.txt lista un nombre no permitido: '$nombre'"
        }
        if ($nombre -eq "MANIFIESTO.txt") { throw "MANIFIESTO.txt se lista a si mismo" }
        if ($items.ContainsKey($nombre)) { throw "MANIFIESTO.txt repite $nombre" }
        $items[$nombre] = @{ Hash = $hashM.ToLower(); Bytes = [int64]$bytesM }
    }
    foreach ($req in @("datos.sql.gz", "csv.zip", "LEEME.txt")) {
        if (-not $items.ContainsKey($req)) { throw "MANIFIESTO.txt no lista $req" }
    }
    return $items
}

$patron = '^\d{4}-\d{2}-\d{2}_\d{6}$'
$codigo = 0

try {
    Log "=== Inicio (ultimos $Copias paquetes) ==="

    # El ls remoto lista la base de la subcuenta (granja-datos). El patron deja
    # afuera .tmp-* y .ssh.
    $todas = @(Remoto "ls" | Where-Object { $_ -match $patron })
    if ($todas.Count -eq 0) { throw "el Storage Box no lista ningun paquete" }
    $elegidas = @($todas | Sort-Object -Descending | Select-Object -First $Copias)

    $nuevas = 0
    foreach ($copia in $elegidas) {
        $final = Join-Path $Destino $copia
        if (Test-Path $final) { continue }

        try {
            Remove-Item $parcial -Recurse -Force -ErrorAction SilentlyContinue
            New-Item -ItemType Directory $parcial | Out-Null
            Set-Content $lote @("lcd `"$parcial`"", "get $copia/*") -Encoding ASCII
            $salida = & sftp.exe @sftpOpts -b $lote $remoto 2>&1
            if ($LASTEXITCODE -ne 0) { throw "sftp fallo: $salida" }

            $manifiesto = LeerManifiesto $parcial

            foreach ($nombre in $manifiesto.Keys) {
                $local = Join-Path $parcial $nombre
                if (-not (Test-Path -LiteralPath $local -PathType Leaf)) { throw "falta $nombre" }
                $tam = (Get-Item -LiteralPath $local).Length
                if ($tam -ne $manifiesto[$nombre].Bytes) {
                    throw "$nombre incompleto: $tam de $($manifiesto[$nombre].Bytes) bytes"
                }
                $hash = (Get-FileHash -LiteralPath $local -Algorithm SHA256).Hash.ToLower()
                if ($hash -ne $manifiesto[$nombre].Hash) { throw "$nombre no coincide con su SHA256" }
            }

            # Nada que el manifiesto no liste (salvo el propio manifiesto).
            foreach ($f in (Get-ChildItem $parcial -Force)) {
                if ($f.Name -ne "MANIFIESTO.txt" -and -not $manifiesto.ContainsKey($f.Name)) {
                    throw "archivo no listado en el manifiesto: $($f.Name)"
                }
            }

            Move-Item $parcial $final -ErrorAction Stop
            if (-not (Test-Path -LiteralPath $final -PathType Container)) { throw "no se pudo mover a $final" }
        }
        catch {
            Remove-Item $parcial -Recurse -Force -ErrorAction SilentlyContinue
            throw "paquete ${copia}: $_"
        }
        $mb = [math]::Round(($manifiesto.Values | ForEach-Object { $_.Bytes } | Measure-Object -Sum).Sum / 1MB, 1)
        Log "  bajado $copia ($($manifiesto.Count) archivos, $mb MB)"
        $nuevas++
    }

    # Retencion local por cantidad: solo carpetas con formato de fecha, nunca
    # otra cosa (ni _parcial ni bajada.log).
    $locales = @(Get-ChildItem $Destino -Directory | Where-Object { $_.Name -match $patron } |
                 Sort-Object Name -Descending)
    if ($locales.Count -gt $Copias) {
        foreach ($d in ($locales | Select-Object -Skip $Copias)) {
            Remove-Item $d.FullName -Recurse -Force
            Log "  borrado por antiguo: $($d.Name)"
        }
    }

    $total = @(Get-ChildItem $Destino -Directory | Where-Object { $_.Name -match $patron }).Count
    Log "=== OK: $nuevas nuevos, $total paquetes en $Destino ==="
}
catch {
    Log "FALLO: $_"
    $codigo = 1
}
finally {
    Remove-Item $lote -ErrorAction SilentlyContinue
}

if ($codigo -eq 0) { Ping "" } else { Ping "/fail" }
exit $codigo
