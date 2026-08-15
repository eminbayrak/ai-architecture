# Jira skill (Data Center)

Harness-agnostic Agent Skill. One launcher picks a runner already on the machine. Nobody installs extra programs.

Windows `scripts/jira.cmd` fallbacks:

1. Windows PowerShell (`jira.ps1`) — built-in (Git Bash often lacks `dirname`)
2. Git Bash / MSYS2 bash, if already present (not WSL `System32\bash.exe`)
3. Python stdlib (`jira.py`) — only if `python` is already on PATH

Override: `set JIRA_RUNNER=powershell`

macOS / Linux: `scripts/jira` (bash + curl).

## Mint a PAT

In your company Jira (Data Center / Server): profile → Personal Access Tokens → create. SSO passwords will not work.

**macOS / Linux**

```bash
mkdir -p ~/.config/atlassian
# one line, the PAT only
chmod 600 ~/.config/atlassian/jira.token
export JIRA_BASE=https://jira.example.com
```

**Windows** (PowerShell)

```powershell
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.config\atlassian" | Out-Null
# one line, the PAT only, in:
#   $env:USERPROFILE\.config\atlassian\jira.token
$env:JIRA_BASE = "https://jira.example.com"
```

Optional: `JIRA_TOKEN` in the environment. Or put this in a repo-root `.env` (same folder as `.poolside`, not `.env.txt`):

```
JIRA_BASE=https://jira.example.com
```

The launcher walks up from the script and from the current directory, so a cwd of `C:\projects` still finds `C:\projects\fde_skills\.env`. `JIRA_ENV_FILE` overrides the path.

## Smoke test

```bash
# macOS / Linux
./skills/jira/scripts/jira get PROJ-123
```

```bat
REM Windows
skills\jira\scripts\jira.cmd get PROJ-123
```

## Chat phrases

- "Move PROJ-123 to In Progress"
- "Comment on https://jira.example.com/browse/PROJ-123 that the eval harness merged"
- "Create a Jira in PROJ: Need PHI-safe RAG evals"

## Poolside

This repo exposes `skills/jira` to Poolside via `python scripts/link-skills.py` (Windows: directory junction, no elevation). On Windows, Poolside should run `scripts\jira.cmd` (it falls back). Do not generate a new Python HTTP client.

Tests live in `skills/jira/tests/test_jira_skill.py`.

## Cursor / Claude

```bash
ln -s "$PWD/skills/jira" ~/.claude/skills/jira
ln -s "$PWD/skills/jira" .cursor/skills/jira
```
