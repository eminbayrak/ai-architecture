# FDE KB launcher for Windows. Finds uv + a Python that can load sqlite
# extensions, then runs fde_kb.py. Poolside should call fde-kb.cmd, which
# delegates here. uv uses UV_DEFAULT_INDEX (FDE_KB_UV_INDEX). Public PyPI
# is not used. Do not print secrets. Do not enable tracing.

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.UTF8Encoding]::new()
$OutputEncoding = [Console]::OutputEncoding
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Script = Join-Path $Here "fde_kb.py"
$UvIndexHint = "fde-kb: FDE_KB_UV_INDEX / UV_DEFAULT_INDEX is not set. Point it at the internal package index (sqlite-vec, model2vec). Public PyPI is not used. Development only: FDE_KB_ALLOW_PUBLIC_INDEX=1."

function Import-KvFile([string]$File) {
  if (-not (Test-Path -LiteralPath $File)) { return }
  Get-Content -LiteralPath $File | ForEach-Object {
    $line = $_.Trim().TrimStart([char]0xFEFF)
    if ($line.StartsWith("export ")) { $line = $line.Substring(7).Trim() }
    if ($line -eq "" -or $line.StartsWith("#") -or $line -notmatch "=") { return }
    $eq = $line.IndexOf("=")
    $key = $line.Substring(0, $eq).Trim()
    $val = $line.Substring($eq + 1).Trim().Trim("'").Trim('"')
    if ($key -notin @("UV_DEFAULT_INDEX", "UV_INDEX_URL", "FDE_KB_UV_INDEX", "FDE_KB_ALLOW_PUBLIC_INDEX")) {
      return
    }
    $existing = [Environment]::GetEnvironmentVariable($key, "Process")
    if ($null -eq $existing) {
      Set-Item -Path "Env:$key" -Value $val
    }
  }
}

function Import-DotEnvWalk([string]$Start) {
  $cur = $Start
  if (-not $cur) { return }
  if (Test-Path -LiteralPath $cur -PathType Leaf) {
    $cur = Split-Path -Parent $cur
  }
  for ($i = 0; $i -lt 8; $i++) {
    if (-not $cur) { break }
    Import-KvFile (Join-Path $cur ".env")
    $parent = Split-Path -Parent $cur
    if (-not $parent -or $parent -eq $cur) { break }
    $cur = $parent
  }
}

function Ensure-UvIndex {
  if ($env:UV_DEFAULT_INDEX) {
    if (-not $env:UV_INDEX_URL) { $env:UV_INDEX_URL = $env:UV_DEFAULT_INDEX }
    return
  }
  if ($env:FDE_KB_UV_INDEX) {
    $env:UV_DEFAULT_INDEX = $env:FDE_KB_UV_INDEX
    if (-not $env:UV_INDEX_URL) { $env:UV_INDEX_URL = $env:FDE_KB_UV_INDEX }
    return
  }
  $allow = ($env:FDE_KB_ALLOW_PUBLIC_INDEX + "").ToLowerInvariant()
  if ($allow -in @("1", "true", "yes", "on")) { return }
  [Console]::Error.WriteLine($UvIndexHint)
  exit 1
}

function Test-SqliteExt([string]$Exe) {
  if (-not $Exe) { return $false }
  if (-not (Test-Path -LiteralPath $Exe)) {
    $cmd = Get-Command $Exe -ErrorAction SilentlyContinue
    if (-not $cmd) { return $false }
    $Exe = $cmd.Source
  }
  try {
    & $Exe -c "import sqlite3,sys; c=sqlite3.connect(':memory:'); sys.exit(0 if hasattr(c,'enable_load_extension') else 1)" 2>$null
    return $LASTEXITCODE -eq 0
  } catch {
    return $false
  }
}

function Find-PythonWithExt {
  $candidates = @()
  if ($env:FDE_KB_PYTHON) { $candidates += $env:FDE_KB_PYTHON }
  $py = Get-Command py -ErrorAction SilentlyContinue
  if ($py) {
    try {
      $resolved = & py -3 -c "import sys; print(sys.executable)" 2>$null
      if ($resolved) { $candidates += $resolved.Trim() }
    } catch { }
  }
  foreach ($name in @("python", "python3")) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { $candidates += $cmd.Source }
  }
  foreach ($exe in $candidates) {
    if (Test-SqliteExt $exe) { return $exe }
  }
  return $null
}

Import-DotEnvWalk (Get-Location).Path
if ($Here) { Import-DotEnvWalk $Here }

$uv = Get-Command uv -ErrorAction SilentlyContinue
$pyext = Find-PythonWithExt

if ($uv) {
  Ensure-UvIndex
  if ($pyext) {
    & uv run --python $pyext --script $Script @args
    exit $LASTEXITCODE
  }
  & uv run --script $Script @args
  exit $LASTEXITCODE
}

if ($env:FDE_KB_PYTHON) {
  & $env:FDE_KB_PYTHON $Script @args
  exit $LASTEXITCODE
}

if ($pyext) {
  & $pyext $Script @args
  exit $LASTEXITCODE
}

$py = Get-Command py -ErrorAction SilentlyContinue
if ($py) {
  & py -3 $Script @args
  exit $LASTEXITCODE
}

$python = Get-Command python -ErrorAction SilentlyContinue
if ($python) {
  & python $Script @args
  exit $LASTEXITCODE
}

[Console]::Error.WriteLine("fde-kb: need uv (preferred) or Python 3.12+ on PATH (py -3 or python)")
exit 1
