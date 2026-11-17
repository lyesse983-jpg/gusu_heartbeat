$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Install Python 3.12, then run this script again." }
    & .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed. Check the terminal output." }
}
if (-not (Test-Path ".env")) { Copy-Item .env.example .env }
Write-Host "Heartbeat Companion: http://127.0.0.1:8765"
Write-Host "Keep this terminal running. Press Ctrl+C to stop."
& .\.venv\Scripts\python.exe -m companion
exit $LASTEXITCODE
