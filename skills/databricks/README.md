# databricks skill

Read-only Databricks access for a coding agent (Poolside, or any Agent Skills harness).

## What the user does

Nothing up front. On the first Databricks question, the agent runs `dbx doctor`, then:

1. Installs or upgrades the Databricks CLI (`winget` on Windows, Homebrew on macOS).
2. Asks for the workspace URL, if the user has no profile yet.
3. Runs `dbx login`. The user signs in with company SSO in the browser.

The CLI stores the login in `~/.databrickscfg` and refreshes it without user action. Existing profiles with a PAT keep working as they are.

## How it works

`scripts/dbx.py` uses only the Python standard library. It calls the official Databricks CLI for everything that touches Databricks.

| Part | Uses |
|---|---|
| Auth | `databricks auth login` (OAuth in the browser). PAT only as a fallback. |
| SQL on a SQL warehouse | `databricks experimental aitools tools query` (Statement Execution API) |
| SQL on an all-purpose cluster | `databricks api` with the Command Execution API 1.2 |
| Table columns | `databricks experimental aitools tools discover-schema` |

On top of the CLI, `dbx.py` adds a read-only guard, a default row limit, a compute choice for each profile and cluster support.

Why no JDBC: the Databricks JDBC driver is a Java client for the same APIs. It adds a JVM and a jar to install, and gives nothing an agent needs.

## Install (Windows, new user)

Run from the root of the repo that holds the MCP server (`mcp\mcp_tools.yaml`), in PowerShell:

```powershell
Invoke-WebRequest https://github.com/eminbayrak/ai-architecture/archive/refs/heads/add-databricks-skill.zip -OutFile $env:TEMP\dbxskill.zip -UseBasicParsing
Expand-Archive $env:TEMP\dbxskill.zip $env:TEMP\dbxskill -Force
Copy-Item $env:TEMP\dbxskill\ai-architecture-add-databricks-skill\skills\databricks knowledge-base\domains\fde\skills -Recurse -Force
.venv\Scripts\python.exe knowledge-base\domains\fde\skills\databricks\scripts\register_mcp.py mcp\mcp_tools.yaml
```

If the repo still has the old JDBC Databricks skill, delete its folder before the `Copy-Item` line, and remove its tool entries:

```powershell
Remove-Item -Recurse -Force knowledge-base\domains\fde\skills\databricks
# ... Copy-Item as above, then:
.venv\Scripts\python.exe knowledge-base\domains\fde\skills\databricks\scripts\register_mcp.py mcp\mcp_tools.yaml --remove fde-databricks --remove fde-databricks-query --remove fde-databricks-list --remove fde-databricks-status --remove fde-databricks-export
```

If the repo copies skill docs to `.poolside/skills` with a deploy step, delete the old deployed `databricks` folder there and run that step again.

Then restart the MCP server (restart Poolside) and ask it to "check my Databricks setup". The first run installs the Databricks CLI if needed and signs you in with SSO.

Running the same commands again updates the skill. `register_mcp.py` replaces its own entries and keeps the first `.bak`.

## MCP registration

For a FastMCP server that loads Python scripts as tools (one function per tool):

1. Copy this folder to `<repo>/knowledge-base/domains/fde/skills/databricks/`.
2. Copy the entries from `mcp_tools.databricks.yaml` under `tools:` in `<repo>/mcp/mcp_tools.yaml`.
3. Restart the MCP server.

The `script:` paths are relative to the folder of `mcp_tools.yaml`. Change them for another layout.

`scripts/dbx_mcp.py` holds the tool functions. Each one runs `dbx.py` as a child process with a list of arguments and no shell. Nothing prints to the server's stdout, which carries the MCP protocol. The server's Python needs `pydantic`, which FastMCP already installs.

## Files

- `HOWTO.md`: guide for the person who uses the skill, with diagrams.

- `SKILL.md`: instructions for the agent.
- `scripts/dbx`, `scripts/dbx.cmd`: launchers that find Python 3.
- `scripts/dbx.py`: the implementation.
- `scripts/dbx_mcp.py`: MCP tool functions. `mcp_tools.databricks.yaml`: their registration entries.
- `tests/test_dbx.py`: unit tests, and end-to-end tests that run the real CLI against a local fake workspace.
- `tests/test_dbx_mcp.py`: loads the MCP tools the way a script-loading FastMCP server does and calls them through an MCP client.

The skill stores one file of its own: `~/.config/databricks-skill/compute.json`, the saved compute choice for each profile.

## Requirements

- Python 3.9+ (already needed by `scripts/link-skills.py`).
- Databricks CLI 1.9.0+. `dbx doctor --fix` installs it.

## Tests

```bash
uv run pytest skills/databricks/tests
```

The end-to-end tests skip when the Databricks CLI is not installed.

After an install, `tests/live_mcp_check.py` calls every tool through the real MCP server against a signed-in workspace. It prints PASS or FAIL with counts only:

```powershell
.venv\Scripts\python.exe knowledge-base\domains\fde\skills\databricks\tests\live_mcp_check.py mcp\server.py mcp\mcp_tools.yaml ws13
```
