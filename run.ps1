<#
.SYNOPSIS
  Start the Agent Dashboard.
.PARAMETER Setup
  Create backend\.venv and install dependencies, then exit.
.PARAMETER Demo
  Use the built-in fake CLI instead of the real claude / codex.
.PARAMETER Port
  Port to listen on (default 8765).
#>
param(
  [switch]$Setup,
  [switch]$Demo,
  [int]$Port = 8765
)

$ErrorActionPreference = 'Stop'
$backend = Join-Path $PSScriptRoot 'backend'
$python = Join-Path $backend '.venv\Scripts\python.exe'

if ($Setup -or -not (Test-Path $python)) {
  Write-Host 'Creating backend\.venv and installing dependencies...'
  python -m venv (Join-Path $backend '.venv')
  & $python -m pip install --disable-pip-version-check -q -e "$backend[dev]"
  if ($Setup) { Write-Host 'Done. Run .\run.ps1 to start.'; exit 0 }
}

$env:DASH_PORT = "$Port"
if ($Demo) { $env:DASH_FAKE_CLI = '1' } else { Remove-Item Env:DASH_FAKE_CLI -ErrorAction SilentlyContinue }

Push-Location $backend
try {
  & $python -m app
} finally {
  Pop-Location
}
