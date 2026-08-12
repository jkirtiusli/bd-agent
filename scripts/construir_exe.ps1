# Construye agente-bdcopy.exe. HAY QUE CORRERLO EN WINDOWS:
# PyInstaller no compila cruzado, un build hecho en Linux produce un binario
# de Linux. Alternativa sin tener una PC Windows a mano: el workflow de
# GitHub Actions (.github/workflows/exe.yml), que construye en un runner
# windows-latest y publica el .exe.
$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
Push-Location $repo
try {
    Write-Host ">> Entorno virtual limpio (para no empaquetar de mas)"
    if (-not (Test-Path ".venv-build")) { python -m venv .venv-build }
    $py = ".\.venv-build\Scripts\python.exe"

    & $py -m pip install --quiet --upgrade pip
    & $py -m pip install --quiet -r requirements.txt pyinstaller

    Write-Host ">> Tests antes de empaquetar"
    & $py -m pip install --quiet pytest
    & $py -m pytest tests/ -q
    if ($LASTEXITCODE -ne 0) { throw "los tests fallaron: no se empaqueta" }

    Write-Host ">> Construyendo"
    Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
    & $py -m PyInstaller --clean --noconfirm agente-bdcopy.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller fallo" }

    $exe = Join-Path $repo "dist\agente-bdcopy.exe"
    $mb  = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host ""
    Write-Host "OK: $exe ($mb MB)" -ForegroundColor Green

    Write-Host ">> Humo: que el ejecutable arranque"
    & $exe --version
    if ($LASTEXITCODE -ne 0) { throw "el .exe no arranca" }

    Write-Host ""
    Write-Host "Para instalarlo en una granja:"
    Write-Host "  1. copiar agente-bdcopy.exe a C:\farmapi\"
    Write-Host "  2. agente-bdcopy.exe --config C:\farmapi\config.yaml --configurar"
    Write-Host "  3. editar granja y token en C:\farmapi\config.yaml"
    Write-Host "  4. agente-bdcopy.exe --config C:\farmapi\config.yaml --diagnostico"
    Write-Host "  5. scripts\instalar_tarea.bat  (como Administrador)"
}
finally { Pop-Location }
