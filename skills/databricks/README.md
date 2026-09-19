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
| Plain-English questions | `databricks genie ask` |

On top of the CLI, `dbx.py` adds a read-only guard, a default row limit, a compute choice for each profile and cluster support.

Why no JDBC: the Databricks JDBC driver is a Java client for the same APIs. It adds a JVM and a jar to install, and gives nothing an agent needs.

## MCP registration

For a FastMCP server that loads Python scripts as tools (one function per tool):

1. Copy this folder to `<repo>/domains/fde/skills/databricks/`.
2. Copy the entries from `mcp_tools.databricks.yaml` under `tools:` in `<repo>/mcp/mcp_tools.yaml`.
3. Restart the MCP server.

The `script:` paths are relative to the folder of `mcp_tools.yaml`. Change them for another layout.

`scripts/dbx_mcp.py` holds the tool functions. Each one runs `dbx.py` as a child process with a list of arguments and no shell. Nothing prints to the server's stdout, which carries the MCP protocol. The server's Python needs `pydantic`, which FastMCP already installs.

## Files

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
