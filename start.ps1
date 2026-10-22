$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$chatPython = Get-Command python -ErrorAction SilentlyContinue
if (-not $chatPython) {
    Write-Host 'Python 3.11 or newer is required.'
    exit 1
}
Write-Host 'Open http://127.0.0.1:8765 in your browser.'
& $chatPython.Source (Join-Path $PSScriptRoot 'server.py')
