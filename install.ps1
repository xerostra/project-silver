# Project Silver installer for Windows. Paste this into PowerShell:
#   powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/xerostra/project-silver/main/install.ps1 | iex"
# It installs uv (if missing) and Silver for your user only, adds a Start-menu shortcut and opens
# the dashboard. No admin rights. Run it again to update.
$ErrorActionPreference = 'Stop'
$source = 'project-silver @ https://github.com/xerostra/project-silver/archive/refs/heads/main.zip'

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host 'Installing uv, the tool Silver runs on...'
    powershell -ExecutionPolicy ByPass -c 'irm https://astral.sh/uv/install.ps1 | iex'
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

Write-Host ''
Write-Host 'Which server do you play on?'
Write-Host '  1  Asia'
Write-Host '  2  Americas'
Write-Host '  3  Europe'
$server = @{ '1' = 'asia'; '2' = 'americas'; '3' = 'europe' }[(Read-Host 'Type 1, 2 or 3').Trim()]
if (-not $server) { $server = 'asia'; Write-Host 'Using Asia.' }

Write-Host 'Installing Project Silver...'
uv tool install --force --reinstall --refresh --python 3.12 $source
if ($LASTEXITCODE) { throw 'Installing Silver failed (see above).' }
uv tool update-shell *> $null  # so `silver` works in new terminals too

$silver = Join-Path (uv tool dir --bin) 'silver.exe'
& $silver --server $server shortcut
Write-Host ''
Write-Host "Done. Open Silver from the Start menu any time. Starting it now ($server)..."
Write-Host 'The first start downloads game data and price history: give it a minute or two.'
Write-Host 'Silver runs in its own console window: keep that open while you use it, close it to stop.'
Start-Process $silver -ArgumentList '--server', $server
