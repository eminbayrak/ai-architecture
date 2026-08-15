@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
set "HERE=%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

REM Windows: PowerShell first (same pattern as the Jira skill). Git Bash is
REM often on PATH without the tools uv needs. !ERRORLEVEL! is required:
REM %ERRORLEVEL% inside parentheses is expanded when the block is parsed.
REM uv uses UV_DEFAULT_INDEX (FDE_KB_UV_INDEX). Public PyPI is not used.

if not defined UV_DEFAULT_INDEX if defined FDE_KB_UV_INDEX (
  set "UV_DEFAULT_INDEX=%FDE_KB_UV_INDEX%"
)
if defined UV_DEFAULT_INDEX if not defined UV_INDEX_URL (
  set "UV_INDEX_URL=%UV_DEFAULT_INDEX%"
)

if /i "%FDE_KB_RUNNER%"=="uv" goto :try_uv
if /i "%FDE_KB_RUNNER%"=="python" goto :try_python

:try_powershell
if exist "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" (
  "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%HERE%fde-kb.ps1" %*
  exit /b !ERRORLEVEL!
)
where pwsh >nul 2>&1
if not errorlevel 1 (
  pwsh -NoProfile -ExecutionPolicy Bypass -File "%HERE%fde-kb.ps1" %*
  exit /b !ERRORLEVEL!
)
if /i "%FDE_KB_RUNNER%"=="powershell" goto :fail

:try_uv
where uv >nul 2>&1
if not errorlevel 1 (
  if not defined UV_DEFAULT_INDEX if defined FDE_KB_UV_INDEX (
    set "UV_DEFAULT_INDEX=!FDE_KB_UV_INDEX!"
    set "UV_INDEX_URL=!FDE_KB_UV_INDEX!"
  )
  if defined UV_DEFAULT_INDEX if not defined UV_INDEX_URL set "UV_INDEX_URL=!UV_DEFAULT_INDEX!"
  if not defined UV_DEFAULT_INDEX (
    if /i not "!FDE_KB_ALLOW_PUBLIC_INDEX!"=="1" if /i not "!FDE_KB_ALLOW_PUBLIC_INDEX!"=="true" (
      echo fde-kb: FDE_KB_UV_INDEX / UV_DEFAULT_INDEX is not set. Point it at the internal package index (sqlite-vec, model2vec). Public PyPI is not used. Development only: FDE_KB_ALLOW_PUBLIC_INDEX=1.
      exit /b 1
    )
  )
  uv run --script "%HERE%fde_kb.py" %*
  exit /b !ERRORLEVEL!
)
if /i "%FDE_KB_RUNNER%"=="uv" goto :fail

:try_python
if defined FDE_KB_PYTHON (
  "%FDE_KB_PYTHON%" "%HERE%fde_kb.py" %*
  exit /b !ERRORLEVEL!
)
where py >nul 2>&1
if not errorlevel 1 (
  py -3 "%HERE%fde_kb.py" %*
  exit /b !ERRORLEVEL!
)
where python >nul 2>&1
if not errorlevel 1 (
  python "%HERE%fde_kb.py" %*
  exit /b !ERRORLEVEL!
)

:fail
echo fde-kb: need uv (preferred) or Python 3.12+ on PATH.
echo On Windows, fde-kb.cmd uses PowerShell to launch uv. Install uv from https://docs.astral.sh/uv/
echo Optional: set FDE_KB_RUNNER=powershell^|uv^|python
exit /b 1
