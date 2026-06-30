$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

$config = Get-Content -Raw ".\varyn.config.json" | ConvertFrom-Json
$hostAddress = if ($env:VARYN_AGENT_HOST) { $env:VARYN_AGENT_HOST } else { [string]$config.runtime.backend_host }
$port = if ($env:VARYN_AGENT_PORT) { $env:VARYN_AGENT_PORT } else { [string]$config.runtime.backend_port }

if (-not (Test-Path ".venv")) {
  py -3.12 -m venv .venv
}

& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt
& ".\.venv\Scripts\python.exe" -m uvicorn main:app --host $hostAddress --port $port
