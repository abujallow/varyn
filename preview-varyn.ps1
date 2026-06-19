$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$nextBin = Join-Path $root "node_modules\next\dist\bin\next"
$buildId = Join-Path $root ".next\BUILD_ID"

if (-not (Test-Path -LiteralPath $nextBin)) {
  Write-Host "Dependencies are missing. Installing from package-lock.json..."
  npm.cmd ci
}

if (-not (Test-Path -LiteralPath $buildId)) {
  Write-Host "Production build is missing. Building once before preview..."
  npm.cmd run build
}

function Test-PortInUse {
  param([int] $Port)

  $pattern = "^\s*TCP\s+\S*[:.]$Port\s+\S+\s+LISTENING\s+\d+\s*$"
  $matches = netstat -ano -p tcp | Select-String -Pattern $pattern
  return $null -ne $matches
}

$port = 3200
while (Test-PortInUse -Port $port) {
  $port += 1
}

Write-Host ""
Write-Host "Varyn preview is starting."
Write-Host "Open this URL: http://localhost:$port"
Write-Host "Press Ctrl+C in this window to stop the preview."
Write-Host ""

node $nextBin start -p $port
