@echo off
setlocal
REM Windows launcher. Runs dbx.py with the first Python 3 that works.
REM "python" can be the Microsoft Store stub. It exits with an error, so the version test skips it.
set "HERE=%~dp0"
py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 (
  py -3 "%HERE%dbx.py" %*
  exit /b %ERRORLEVEL%
)
python -c "import sys; sys.exit(sys.version_info[0] != 3)" >nul 2>&1
if not errorlevel 1 (
  python "%HERE%dbx.py" %*
  exit /b %ERRORLEVEL%
)
echo dbx: Python 3 not found. Ask IT for Python 3, or run: winget install Python.Python.3.12 1>&2
exit /b 1
