# Environment doctor. Run from a shell that has dot-sourced scripts\env.ps1:
#   . .\scripts\env.ps1 ; .\scripts\doctor.ps1
# Deliberately does NOT set the environment itself: it judges what the shell gives it.
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $py)) { Write-Output "FAIL  project venv missing: $py"; Write-Output 'DOCTOR: RED'; exit 1 }
& $py -X utf8 (Join-Path $PSScriptRoot 'doctor.py')
exit $LASTEXITCODE
