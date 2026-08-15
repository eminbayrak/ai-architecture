@echo off
setlocal EnableExtensions
set "HERE=%~dp0"

REM Windows: PowerShell first. Git Bash is often on PATH without dirname/coreutils
REM (that produced: "dirname: command not found"). Bash/Python are optional later.

if /i "%JIRA_RUNNER%"=="bash" goto :try_bash
if /i "%JIRA_RUNNER%"=="python" goto :try_python
if /i "%JIRA_RUNNER%"=="powershell" goto :try_powershell

:try_powershell
if exist "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" (
  "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%HERE%jira.ps1" %*
  exit /b %ERRORLEVEL%
)
where pwsh >nul 2>&1
if not errorlevel 1 (
  pwsh -NoProfile -File "%HERE%jira.ps1" %*
  exit /b %ERRORLEVEL%
)
if /i "%JIRA_RUNNER%"=="powershell" goto :fail

:try_bash
set "BASH="
if defined MSYS2_ROOT if exist "%MSYS2_ROOT%\usr\bin\bash.exe" set "BASH=%MSYS2_ROOT%\usr\bin\bash.exe"
if not defined BASH if exist "C:\msys64\usr\bin\bash.exe" set "BASH=C:\msys64\usr\bin\bash.exe"
if not defined BASH if exist "%ProgramFiles%\Git\bin\bash.exe" set "BASH=%ProgramFiles%\Git\bin\bash.exe"
if not defined BASH if exist "%ProgramFiles%\Git\usr\bin\bash.exe" set "BASH=%ProgramFiles%\Git\usr\bin\bash.exe"
if defined BASH (
  "%BASH%" "%HERE%jira" %*
  exit /b %ERRORLEVEL%
)
if /i "%JIRA_RUNNER%"=="bash" goto :fail

:try_python
where py >nul 2>&1
if not errorlevel 1 (
  py -3 "%HERE%jira.py" %*
  exit /b %ERRORLEVEL%
)
where python >nul 2>&1
if not errorlevel 1 (
  python "%HERE%jira.py" %*
  exit /b %ERRORLEVEL%
)

:fail
echo Jira skill: no runner found. On Windows, PowerShell is enough (built-in).
echo Optional: set JIRA_RUNNER=powershell^|bash^|python
exit /b 1
