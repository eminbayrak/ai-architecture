@echo off
setlocal
set "PY=%~dp0llm_wiki.py"
if defined LLM_WIKI_PYTHON (
  "%LLM_WIKI_PYTHON%" "%PY%" %*
  exit /b %ERRORLEVEL%
)
where py >nul 2>&1 && (
  py -3 "%PY%" %*
  exit /b %ERRORLEVEL%
)
where python >nul 2>&1 && (
  python "%PY%" %*
  exit /b %ERRORLEVEL%
)
echo llm-wiki: need Python 3.12+ on PATH 1>&2
exit /b 1
