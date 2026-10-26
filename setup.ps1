# One-time setup on Windows: .venv with storyaudio, then a health check.
# Run: powershell -ExecutionPolicy Bypass -File setup.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Python 3.11+ is needed (tomllib). uv is used when present: it is faster.
if (Get-Command uv -ErrorAction SilentlyContinue) {
    uv venv --allow-existing -p ">=3.11" .venv
    if ($LASTEXITCODE -ne 0) { exit 1 }
    uv pip install --python .venv\Scripts\python.exe -e ".[dev]"
} else {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        Write-Host "Python 3.11+ not found. Install it: winget install Python.Python.3.12"
        exit 1
    }
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Python 3.11+ needed. Install it: winget install Python.Python.3.12"
        exit 1
    }
    if (-not (Test-Path .venv)) { python -m venv .venv }
    .venv\Scripts\python.exe -m pip install --quiet --upgrade pip
    .venv\Scripts\python.exe -m pip install --quiet -e ".[dev]"
}
if ($LASTEXITCODE -ne 0) { exit 1 }

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host "ffmpeg not found. Install it, then open a new terminal: winget install Gyan.FFmpeg"
}

Write-Host ""
.venv\Scripts\storyaudio.exe doctor
Write-Host ""
Write-Host "Setup done. CLI: .venv\Scripts\storyaudio"
