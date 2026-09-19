"""MCP tool functions for the Databricks skill.

A FastMCP server that loads Python scripts as tools (register each function in
mcp_tools.yaml with `script:` pointing here and `function_name:` set to it) calls
these functions in its own process. FastMCP builds each tool's input schema from
the function signature below.

Every function runs dbx.py as a child process:
- The MCP stdio stream stays clean. Nothing here prints, and children never see the
  server's stdin or stdout.
- Arguments go as a list, never through a shell, so Windows quoting cannot change SQL.
- SQL goes on the child's stdin, so its length and characters do not matter.

Every function returns {"success": bool, ...}.
"""

# No `from __future__ import annotations`: the MCP server wraps these functions in its own
# module, where string annotations would not resolve. Needs Python 3.10+.
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field

# The MCP server exec()s this file with __file__ set to its real path.
DBX = Path(__file__).resolve().parent / "dbx.py"
# Covers a stopped cluster start (up to 15 min) plus the query (up to 10 min).
TIMEOUT_S = 26 * 60

Workspace = Annotated[
    str | None,
    Field(description="Profile name from ~/.databrickscfg, for example ws13. Leave empty if the user has one workspace."),
]


def _run(args: list[str], stdin: str | None = None, timeout: float = TIMEOUT_S) -> dict[str, Any]:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        r = subprocess.run(
            [sys.executable, str(DBX), *args],
            input=stdin if stdin is not None else "",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Timed out after {int(timeout)} s."}
    out = r.stdout.strip()
    try:
        data: Any = json.loads(out) if out else None
    except ValueError:
        data = out
    result: dict[str, Any] = {"success": r.returncode == 0}
    if r.returncode == 0:
        result["result"] = data
        # dbx writes row counts and progress notes to stderr. CLI warnings are noise here.
        notes = [ln for ln in r.stderr.splitlines() if ln.strip() and not ln.startswith("Warn:")]
        if notes:
            result["notes"] = notes
    else:
        result["error"] = (r.stderr.strip() or out).removeprefix("dbx: ")
        if data:
            result["output"] = data
    return result


def _profile(workspace: str | None) -> list[str]:
    return ["-p", workspace] if workspace else []


def databricks_setup(
    fix: Annotated[bool, Field(description="Install or upgrade the Databricks CLI if it is missing or too old.")] = False,
) -> dict[str, Any]:
    """Check the Databricks CLI and every workspace login. Run this first on a new machine."""
    return _run(["doctor", *(["--fix"] if fix else [])], timeout=15 * 60)


def databricks_login(
    workspace_url: Annotated[
        str, Field(description="Workspace URL from the browser address bar, or an existing profile name.")
    ],
    name: Annotated[str | None, Field(description="Short profile name to save, for example ws13.")] = None,
) -> dict[str, Any]:
    """Sign the user in to a workspace. A browser tab opens for company SSO.

    Returns at once. Tell the user to finish the sign-in in the browser, then call
    databricks_setup to confirm the workspace shows OK.
    """
    args = [sys.executable, str(DBX), "login", workspace_url, *(["--name", name] if name else [])]
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    # Detached: the browser wait can be longer than the MCP client's tool timeout.
    subprocess.Popen(
        args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=flags, start_new_session=os.name != "nt",
    )
    return {
        "success": True,
        "result": "A browser tab is opening for sign-in. After the user finishes, call databricks_setup.",
    }


def databricks_sql(
    query: Annotated[
        str,
        Field(description="One read-only statement: SELECT, WITH, VALUES, SHOW, DESCRIBE or EXPLAIN. "
                          "Use :name markers for values."),
    ],
    workspace: Workspace = None,
    params: Annotated[
        dict[str, str] | None,
        Field(description="Values for :name markers. Key 'name' or 'name:TYPE' (INT, DATE, ...), value as text."),
    ] = None,
    limit: Annotated[int, Field(description="Row limit added when the query has none. 0 means no limit.")] = 1000,
) -> dict[str, Any]:
    """Run one read-only SQL statement on Databricks and return the rows as a list of objects."""
    args = ["sql", "-", *_profile(workspace), "--limit", str(limit)]
    for key, value in (params or {}).items():
        args += ["--param", f"{key}={value}"]
    return _run(args, stdin=query)


def databricks_schema(
    tables: Annotated[list[str], Field(description="Full table names: catalog.schema.table")],
    workspace: Workspace = None,
) -> dict[str, Any]:
    """Show columns, types and descriptions for tables. Use it before you write SQL."""
    return _run(["schema", *tables, *_profile(workspace)])


def databricks_compute(
    workspace: Workspace = None,
    use: Annotated[
        str | None, Field(description="Save a choice: warehouse:<id> or cluster:<id>. Leave empty to list the options.")
    ] = None,
) -> dict[str, Any]:
    """List the SQL warehouses and clusters for a workspace, or save which one SQL runs on."""
    return _run(["compute", *_profile(workspace), *(["--use", use] if use else [])])
