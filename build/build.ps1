# Compila ClaudeUsageMonitor.exe y, si Inno Setup esta disponible, el instalador.
#
#   .\build\build.ps1                 # exe + instalador
#   .\build\build.ps1 -SoloExe        # solo el .exe portable
#
# Funciona con cualquier Python 3.11+. Se prefiere uno de python.org: el de
# Microsoft Store corre en un contenedor que redirige %APPDATA% y el registro
# (molesto al ejecutar desde el codigo fuente), aunque el .exe resultante ya
# lleva su propia DLL de Python y no sufre esa redireccion.

param(
    [switch]$SoloExe,
    [string]$Python = ""
)

# PyInstaller escribe su registro en stderr: con ErrorActionPreference = Stop,
# PowerShell 5.1 lo tomaria por un fallo. Se comprueba $LASTEXITCODE a mano.
$ErrorActionPreference = "Continue"
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz

function Assert-Ok([string]$paso) {
    if ($LASTEXITCODE -ne 0) { throw "$paso fallo (codigo $LASTEXITCODE)." }
}

function Find-Python {
    if ($Python) { return $Python }
    # El lanzador py apunta a las instalaciones de python.org
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $ruta = (& py -3 -c "import sys; print(sys.executable)" 2>$null)
        if ($LASTEXITCODE -eq 0 -and $ruta -and $ruta -notmatch "WindowsApps") { return $ruta.Trim() }
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) {
        if ($cmd.Source -match "WindowsApps") {
            Write-Warning "Usando el Python de Microsoft Store. El .exe saldra bien, pero para"
            Write-Warning "ejecutar desde el codigo fuente conviene: winget install Python.Python.3.13"
        }
        return $cmd.Source
    }
    throw "No se encontro Python. Instalalo con: winget install Python.Python.3.13 (o pasa -Python <ruta>)"
}

$python = Find-Python
Write-Host "Python de compilacion: $python"

$venv = Join-Path $raiz ".venv-build"
if (-not (Test-Path $venv)) {
    Write-Host "Creando entorno de compilacion..."
    & $python -m venv $venv
    Assert-Ok "La creacion del entorno virtual"
}
$vpy = Join-Path $venv "Scripts\python.exe"

Write-Host "Instalando dependencias..."
& $vpy -m pip install --quiet --upgrade pip
& $vpy -m pip install --quiet -r requirements.txt pyinstaller
Assert-Ok "La instalacion de dependencias"

Write-Host "Generando icono..."
& $vpy tools\make_icon.py
Assert-Ok "La generacion del icono"

Write-Host "Comprobaciones previas..."
& $vpy tools\selftest.py
Assert-Ok "Las comprobaciones previas"

# La app vive en la bandeja: si esta corriendo, el .exe esta en uso y no se
# puede sobrescribir.
Get-Process ClaudeUsageMonitor -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 400

Write-Host "Compilando el .exe..."
# Rutas absolutas: con --specpath, PyInstaller resuelve las relativas respecto
# a la carpeta del .spec, no a la del proyecto.
& $vpy -m PyInstaller --noconfirm --clean --log-level WARN `
    --onefile --noconsole `
    --name ClaudeUsageMonitor `
    --icon (Join-Path $raiz "assets\app.ico") `
    --paths (Join-Path $raiz "src") `
    --distpath (Join-Path $raiz "dist") `
    --workpath (Join-Path $raiz ".pyinstaller") `
    --specpath (Join-Path $raiz ".pyinstaller") `
    --hidden-import pystray._win32 `
    --exclude-module numpy --exclude-module pytest `
    (Join-Path $raiz "app.py")
Assert-Ok "PyInstaller"

$exe = Join-Path $raiz "dist\ClaudeUsageMonitor.exe"
if (-not (Test-Path $exe)) { throw "PyInstaller no genero $exe" }
Write-Host "Listo: $exe ($([math]::Round((Get-Item $exe).Length / 1MB, 1)) MB)"

if ($SoloExe) { return }

$iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    # winget lo instala por usuario cuando no hay permisos de administrador
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $iscc) {
    Write-Warning "Inno Setup no esta instalado; se omite el instalador."
    Write-Warning "Instalalo con: winget install JRSoftware.InnoSetup"
    return
}

Write-Host "Compilando el instalador..."
& $iscc "build\installer.iss"
Assert-Ok "Inno Setup"
Write-Host "Instalador listo: dist\ClaudeUsageMonitor-Setup.exe"
