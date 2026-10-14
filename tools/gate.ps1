# One-command quality gate: lint + format + types + tests. Run ONE gate at a time.
# Usage: powershell -NoProfile -File tools/gate.ps1
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\env.ps1"
Set-Location $Root

if (-not (Test-Path $Py)) {
    if (-not $Uv) { Write-Host 'GATE FAIL: uv not found (install it: https://docs.astral.sh/uv/)'; exit 1 }
    Write-Host 'Setting up .venv (uv sync)...'
    & $Uv sync --frozen 2>&1 | Write-Host
    if ($LASTEXITCODE -ne 0) { Write-Host 'GATE FAIL: uv sync'; exit 1 }
}

$steps = @(
    @{ Name = 'ruff check';    Args = @('-m', 'ruff', 'check', '.') },
    @{ Name = 'ruff format';   Args = @('-m', 'ruff', 'format', '--check', '.') },
    @{ Name = 'mypy';          Args = @('-m', 'mypy') },
    @{ Name = 'pytest';        Args = @('-m', 'pytest') }
)

$failed = @()
foreach ($s in $steps) {
    Write-Host "=== $($s.Name) ==="
    & $Py @($s.Args) 2>&1 | Write-Host
    $code = $LASTEXITCODE
    Write-Host "--- $($s.Name): exit $code"
    if ($code -ne 0) { $failed += $s.Name }
}

if ($failed.Count -gt 0) {
    Write-Host "GATE FAIL: $($failed -join ', ')"
    exit 1
}
Write-Host "GATE PASS ($($steps.Count) steps)"
exit 0
