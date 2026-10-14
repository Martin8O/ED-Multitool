# Dot-source this to keep the toolchain, its caches and temp files inside the project folder.
# Usage: . "$PSScriptRoot\env.ps1"
$Root = Split-Path -Parent $PSScriptRoot

$env:UV_CACHE_DIR = Join-Path $Root '.cache\uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $Root '.tools\python'
$env:UV_TOOL_DIR = Join-Path $Root '.tools\uv-tools'
$env:UV_PYTHON_INSTALL_BIN = '0'        # no python shims in ~/.local/bin
$env:UV_PYTHON_INSTALL_REGISTRY = '0'   # no Windows registry entries
$env:UV_PYTHON_PREFERENCE = 'only-managed'
$env:UV_PROJECT_ENVIRONMENT = Join-Path $Root '.venv'
$env:TEMP = Join-Path $Root '.cache\tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force $env:TEMP | Out-Null

$Uv = Join-Path $Root '.tools\uv\uv.exe'
if (-not (Test-Path $Uv)) { $Uv = (Get-Command uv -ErrorAction SilentlyContinue).Source }  # uv installed the usual way
$Py = Join-Path $Root '.venv\Scripts\python.exe'
