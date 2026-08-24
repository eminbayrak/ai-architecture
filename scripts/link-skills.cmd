@echo off
setlocal
set "SCRIPT=%~dp0link-skills.py"
if defined LINK_SKILLS_PYTHON (
  "%LINK_SKILLS_PYTHON%" "%SCRIPT%" %*
  exit /b %ERRORLEVEL%
)
where py >nul 2>&1 && (
  py -3 "%SCRIPT%" %*
  exit /b %ERRORLEVEL%
)
where python >nul 2>&1 && (
  python "%SCRIPT%" %*
  exit /b %ERRORLEVEL%
)
echo link-skills: need Python 3.12+ on PATH 1>&2
exit /b 1
