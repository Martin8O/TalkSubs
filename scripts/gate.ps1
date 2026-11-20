#Requires -Version 7
# The one-command quality gate:  .\scripts\gate.ps1   (proof that it can fail:  .\scripts\gate.ps1 -Canary)
# Sets the project environment itself, refuses to run twice at once (two runs give false greens),
# requires the GPU tests to really run, logs everything, and writes .cache\.gate-ok only when every step passed.
param([switch]$Canary)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'env.ps1')
$env:PARAKEET_STT_REQUIRE_GPU_TESTS = '1'
Set-Location -LiteralPath $root

$local = Join-Path $root '.cache'
$logDir = Join-Path $local 'gate'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$okFile = Join-Path $local '.gate-ok'
$lockPath = Join-Path $local '.gate.lock'
$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $py)) { Write-Output "GATE: RED - project venv missing ($py)"; exit 1 }

$steps = @(
  @{ name = 'ruff check';  args = @('-m', 'ruff', 'check', '.') },
  @{ name = 'ruff format'; args = @('-m', 'ruff', 'format', '--check', '.') },
  @{ name = 'mypy';        args = @('-m', 'mypy') },
  @{ name = 'types fresh'; args = @('scripts\gen_types.py', '--check') },
  @{ name = 'web lint';    args = @('scripts\web_check.py', 'lint') },
  @{ name = 'web types';   args = @('scripts\web_check.py', 'types') },
  @{ name = 'web tests';   args = @('scripts\web_check.py', 'test') },
  @{ name = 'web fresh';   args = @('scripts\web_check.py', 'fresh') },
  @{ name = 'pytest';      args = @('-m', 'pytest', '-p', 'no:cacheprovider') }
)

function Invoke-Steps([string]$log) {
  $failed = @()
  foreach ($s in $steps) {
    "===== $($s.name) =====" | Add-Content -LiteralPath $log -Encoding utf8
    $t0 = Get-Date
    $out = & $py -X utf8 @($s.args) 2>&1 | ForEach-Object { "$_" }
    $code = $LASTEXITCODE
    $out | Add-Content -LiteralPath $log -Encoding utf8
    $secs = [int]((Get-Date) - $t0).TotalSeconds
    $last = ($out | Where-Object { $_.Trim() } | Select-Object -Last 1)
    if ($code -eq 0) { Write-Host ("PASS  {0,-12} {1,4}s  {2}" -f $s.name, $secs, $last) }
    else { Write-Host ("FAIL  {0,-12} {1,4}s  exit {2}: {3}" -f $s.name, $secs, $code, $last); $failed += $s.name }
  }
  return , $failed
}

function Get-TreeDigest {
  # what was actually checked: HEAD plus every uncommitted change, including the contents of new files
  $new = & git -C $root ls-files --others --exclude-standard
  $newHashes = if ($new) { (& git -C $root hash-object -- @($new)) -join "`n" } else { '' }
  $state = (& git -C $root rev-parse HEAD) + "`n" + ((& git -C $root status --porcelain) -join "`n") + "`n" + ((& git -C $root diff HEAD) -join "`n") + "`n" + $newHashes
  $bytes = [System.Text.Encoding]::UTF8.GetBytes($state)
  return ([System.Security.Cryptography.SHA256]::HashData($bytes) | ForEach-Object { $_.ToString('x2') }) -join ''
}

# deliberate faults: each must turn the gate red in the named step
$canaries = @(
  @{ step = 'pytest';     path = 'tests\test_zz_canary_fail.py'; text = "def test_canary() -> None:`n    assert False, `"canary`"`n" },
  @{ step = 'pytest';     path = 'tests\test_zz_canary_import.py'; text = "import canary_module_that_does_not_exist  # noqa: F401`n" },
  @{ step = 'pytest';     path = 'tests\test_zz_canary_plain.py'; text = "from pathlib import Path`n`nfrom tests.test_storage import assert_all_sealed`n`n`ndef test_canary(tmp_path: Path) -> None:`n    (tmp_path / `"jobs`").mkdir()`n    (tmp_path / `"jobs`" / `"talk.pstt.json`").write_text(`"{}`", encoding=`"utf-8`")`n    assert_all_sealed(tmp_path)`n" },
  @{ step = 'ruff check'; path = 'parakeet_stt\_canary_lint.py'; text = "import os`n" },
  @{ step = 'mypy';       path = 'parakeet_stt\_canary_types.py'; text = "x: int = `"not a number`"`n" },
  @{ step = 'types fresh'; path = 'web\src\api\zz_canary.gen.ts'; text = "export type Stale = 1;`n" },
  @{ step = 'web lint';   path = 'web\src\zz_canary_lint.ts'; text = "export function f(): void {`n  debugger;`n}`n" },
  @{ step = 'web types';  path = 'web\src\zz_canary_types.ts'; text = "export const n: number = `"not a number`";`n" },
  @{ step = 'web tests';  path = 'web\src\zz_canary.test.ts'; text = "import { expect, it } from `"vitest`";`nit(`"canary`", () => expect(1).toBe(2));`n" },
  @{ step = 'web fresh';  path = 'parakeet_stt\server\static\zz_canary.html'; text = "<p>stale</p>`n" }
)

try {
  # exclusive handle: a second gate cannot open it while this one runs; Windows frees it if we crash
  $lock = [System.IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'None')
} catch {
  Write-Output 'GATE: REFUSED - another gate run is in progress (one gate at a time).'
  exit 2
}

try {
  Remove-Item -LiteralPath $okFile -Force -ErrorAction SilentlyContinue
  $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
  if ($Canary) {
    $bad = 0
    foreach ($c in $canaries) {
      $target = Join-Path $root $c.path
      if (Test-Path -LiteralPath $target) { Write-Output "CANARY: $($c.path) already exists - refusing"; exit 1 }
      $log = Join-Path $logDir "canary-$stamp-$([IO.Path]::GetFileNameWithoutExtension($c.path)).log"
      try {
        Set-Content -LiteralPath $target -Value $c.text -Encoding utf8 -NoNewline
        Write-Output "--- canary: $($c.path) (expect $($c.step) to fail)"
        $failed = Invoke-Steps $log
      } finally {
        Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
      }
      $okLeft = Test-Path -LiteralPath $okFile
      if ($failed -contains $c.step -and -not $okLeft) { Write-Output "CANARY CAUGHT: $($c.step) went red" }
      else { Write-Output "CANARY MISSED: $($c.step) did not go red (failed: $($failed -join ', '))"; $bad++ }
    }
    $leftovers = $canaries | Where-Object { Test-Path -LiteralPath (Join-Path $root $_.path) }
    if ($leftovers) { Write-Output 'CANARY: a fault file was left behind'; $bad++ }
    if ($bad -gt 0) { Write-Output "CANARIES: $bad MISSED - the gate cannot be trusted"; exit 1 }
    Write-Output "CANARIES: all $($canaries.Count) caught (the gate goes red when it should)"
    exit 0
  }
  $log = Join-Path $logDir "gate-$stamp.log"
  $failed = Invoke-Steps $log
  Write-Output "log: $log"
  if ($failed.Count -gt 0) { Write-Output "GATE: RED ($($failed -join ', '))"; exit 1 }
  "green $(Get-Date -Format o) tree $(Get-TreeDigest)" | Set-Content -LiteralPath $okFile -Encoding utf8
  Write-Output 'GATE: GREEN'
  exit 0
} finally {
  $lock.Dispose()
}
