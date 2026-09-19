"""Live check: call every Databricks tool through the real MCP server, as an agent does.

    python live_mcp_check.py <path/to/mcp/server.py> <path/to/mcp_tools.yaml> <workspace>

Needs a signed-in workspace. Runs only read statements. Prints PASS or FAIL for each
check, with counts only: no hosts, IDs, table names or row values.
Exit code 0 when every check passes. Pytest does not collect this file.
"""

import asyncio
import os
import sys

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

NEW = {"fde-databricks-setup", "fde-databricks-login", "fde-databricks-sql",
       "fde-databricks-schema", "fde-databricks-compute"}
OLD = {"fde-databricks", "fde-databricks-query", "fde-databricks-list",
       "fde-databricks-status", "fde-databricks-export"}

failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failures
    failures += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {name}{f'  ({detail})' if detail else ''}", flush=True)


def first_value(row: dict, keys: tuple[str, ...]) -> str | None:
    for k in keys:
        if row.get(k):
            return row[k]
    return None


def quote(*parts: str) -> str:
    return ".".join(f"`{p}`" for p in parts)


async def main(server: str, config: str, ws: str) -> None:
    # Full environment, as an IDE agent starts the server. dbx needs the user's home and PATH.
    transport = StdioTransport(command=sys.executable, args=[server, "--config", config], env=dict(os.environ))
    async with Client(transport, timeout=30 * 60) as c:
        async def call(tool: str, args: dict) -> dict:
            res = await c.call_tool(tool, args, raise_on_error=False)
            return res.structured_content or {}

        names = {t.name for t in await c.list_tools()}
        check("new tools registered", NEW <= names, f"{len(NEW & names)} of {len(NEW)}")
        check("old JDBC tools gone", not (OLD & names), f"{len(OLD & names)} left")

        r = await call("fde-databricks-setup", {})
        check("setup", r.get("success") is True and "OK" in str(r.get("result")))

        r = await call("fde-databricks-sql", {"query": "SELECT 1 AS ok", "workspace": ws})
        check("sql SELECT 1", r.get("success") is True and str(r["result"][0]["ok"]) == "1")

        r = await call("fde-databricks-sql", {"query": "SELECT :n + 1 AS n", "workspace": ws,
                                              "params": {"n:INT": "41"}})
        check("sql with a typed parameter", r.get("success") is True and str(r["result"][0]["n"]) == "42")

        r = await call("fde-databricks-sql", {"query": "DROP TABLE x", "workspace": ws})
        check("DROP is blocked", r.get("success") is False and "Blocked" in r.get("error", ""))

        r = await call("fde-databricks-sql", {"query": "SELECT 1; DELETE FROM x", "workspace": ws})
        check("second statement is blocked", r.get("success") is False)

        r = await call("fde-databricks-compute", {"workspace": ws})
        check("compute", r.get("success") is True)

        # The plain-English path: find a table the way the agent does, then read it.
        r = await call("fde-databricks-sql", {"query": "SHOW CATALOGS", "workspace": ws})
        catalogs = [first_value(x, ("catalog", "catalog_name")) for x in r.get("result") or []]
        check("SHOW CATALOGS", r.get("success") is True and bool(catalogs), f"{len(catalogs)} catalogs")

        table = None
        for cat in [c for c in catalogs if c and c not in ("system", "samples")][:5]:
            r = await call("fde-databricks-sql", {"query": f"SHOW SCHEMAS IN {quote(cat)}", "workspace": ws})
            schemas = [first_value(x, ("databaseName", "namespace", "schema_name")) for x in r.get("result") or []]
            for sch in [s for s in schemas if s and s != "information_schema"][:5]:
                r = await call("fde-databricks-sql", {"query": f"SHOW TABLES IN {quote(cat, sch)}", "workspace": ws})
                tables = [first_value(x, ("tableName", "table_name")) for x in r.get("result") or []]
                if tables and tables[0]:
                    table = quote(cat, sch, tables[0])
                    break
            if table:
                break
        check("found a table through SHOW", table is not None)
        if table:
            r = await call("fde-databricks-schema", {"tables": [table], "workspace": ws})
            check("schema", r.get("success") is True and bool(r.get("result")))
            r = await call("fde-databricks-sql", {"query": f"SELECT * FROM {table}", "workspace": ws, "limit": 3})
            rows = r.get("result") if r.get("success") else None
            check("SELECT with default limit", isinstance(rows, list) and len(rows) <= 3, f"{len(rows or [])} rows")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    asyncio.run(main(*sys.argv[1:]))
    print("ALL PASSED" if not failures else f"{failures} FAILED")
    sys.exit(1 if failures else 0)
