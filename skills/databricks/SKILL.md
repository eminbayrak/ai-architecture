---
name: databricks
description: Read data from Databricks. Run read-only SQL, look up catalogs, schemas, tables and columns, query metric views, or ask Genie a plain-English data question. Use when the user mentions Databricks, a workspace (ws11, ws13, ...), a catalog.schema.table name, a SQL warehouse, a metric view, Genie, or asks a question about company data in Databricks. Also sets up the Databricks CLI and workspace login on first use.
---

# Databricks (read-only)

## MCP tools first

If the `fde-databricks-*` MCP tools are available, use them, not the launcher. They take the same inputs as the commands below and return `{"success": ..., "result": ...}`.

| Command below | MCP tool |
|---|---|
| `dbx doctor` / `dbx doctor --fix` | `fde-databricks-setup` (`fix: true`) |
| `dbx login <url> --name <n>` | `fde-databricks-login`. It returns at once. Ask the user to finish sign-in in the browser, then call `fde-databricks-setup`. |
| `dbx sql -p <ws> "..." --param k=v` | `fde-databricks-sql` with `query`, `workspace`, `params: {"k": "v"}` |
| `dbx schema -p <ws> <tables>` | `fde-databricks-schema` |
| `dbx ask -p <ws> "..."` | `fde-databricks-ask` |
| `dbx compute -p <ws>` | `fde-databricks-compute` |

With MCP tools, pass SQL as text in `query`. The `--file` rule for Windows below applies only to the launcher.

## Launcher (no MCP)

Every call goes through one launcher. It wraps the official Databricks CLI.

- **Windows:** `<skill>\scripts\dbx.cmd`. From PowerShell, call it as `& "<skill>\scripts\dbx.cmd" ...`.
- **macOS / Linux:** `<skill>/scripts/dbx`

The examples below write `dbx`. Use the launcher for your OS.

No JDBC, no Java, no pip packages. Never write a new HTTP client or a new Python script for Databricks.

## First use on a machine

Run `dbx doctor` first. It checks the CLI and every workspace login. Follow the `Next:` line it prints.

1. **CLI missing or too old:** tell the user you will install it, then run `dbx doctor --fix`.
   It uses `winget` on Windows and Homebrew on macOS. If IT blocks the install, stop and tell the user.
2. **No workspace set up:** ask the user for their workspace URL.
   They copy it from the browser address bar when Databricks is open. Also ask for a short name (for example `ws13`).
   Then run `dbx login <url> --name <name>`. A browser tab opens for company sign-in.
3. **A workspace shows NOT LOGGED IN:** run `dbx login <name>`.
4. **Browser login fails** (company turned off OAuth for the CLI): run `dbx login <name> --pat`.
   It prints one command. The user runs it in their own terminal and types the token there.

Each user has their own workspaces and logins. Never hardcode a workspace URL in this skill.

## Which workspace

Pass `-p <name>` on every command. If the user did not say which workspace, and `dbx doctor` lists more than one, ask.

## Pick the tool for the question

| The user wants | Run |
|---|---|
| A plain-English answer ("revenue by region last quarter", "where is the claims data?") | `dbx ask -p ws13 -s <topic> "<question>"` |
| A business metric that has a metric view | `dbx sql -p ws13 "SELECT region, MEASURE(\`Total Revenue\`) FROM cat.sch.sales_mv GROUP BY region"` |
| An exact query, or you know the tables | `dbx sql -p ws13 "<SELECT ...>"` |
| What catalogs / schemas / tables exist | `dbx sql -p ws13 "SHOW CATALOGS"`, `"SHOW SCHEMAS IN cat"`, `"SHOW TABLES IN cat.sch"` |
| Columns and types | `dbx schema -p ws13 cat.sch.table` |
| Metric views in a schema | `dbx sql -p ws13 "SHOW VIEWS IN cat.sch"`, then `DESCRIBE TABLE EXTENDED` on one of them |

- **Genie (`ask`):** Do not put double quotes inside the question. Reuse the same `-s` label for follow-up questions. Genie prints the SQL it ran. Show that SQL to the user with the answer. If Genie is not turned on for the workspace, or `ask` times out, write the SQL yourself with `sql`. On some workspaces the SQL warehouse API never answers, and Genie needs it.
- **Metric views:** Wrap every measure in `MEASURE()`. `SELECT *` does not work on a metric view.
- **Your own SQL:** Look at the columns with `schema` first. Do not guess column names.

## SQL rules

**On Windows, always put the SQL in a file and pass `--file`.** Inline SQL goes through `cmd.exe` and PowerShell, and they change `%`, `$` and double quotes.

```powershell
Set-Content -Path "$env:TEMP\dbx.sql" -Value @'
SELECT region, count(*) AS n FROM cat.sch.orders WHERE status LIKE '%OPEN%' GROUP BY region
'@
& "<skill>\scripts\dbx.cmd" sql -p ws13 --file "$env:TEMP\dbx.sql"
```

Use a single-quoted here-string (`@' ... '@`) so PowerShell does not change `$`. Any file encoding works.

- One statement per call. Only `SELECT`, `WITH`, `VALUES`, `SHOW`, `DESCRIBE` and `EXPLAIN` run. The launcher blocks everything else before it reaches Databricks.
- Put user values in parameters, never into the SQL text:
  `dbx sql -p ws13 "SELECT * FROM t WHERE region = :r AND day >= :d" --param r=EMEA --param d:DATE=2026-01-01`
- The launcher adds `LIMIT 1000` when the query has no LIMIT. Use `--limit N` to change it, or `--limit 0` for none.
- Output is JSON (a list of rows). Add `--csv` for CSV.

## Compute

`dbx sql` picks compute in this order: a saved choice, then the profile's `http_path`, then the workspace's default SQL warehouse.

- Run `dbx compute -p ws13` to list warehouses and clusters.
- Save one with `dbx compute -p ws13 --use warehouse:<id>`. Prefer a SQL warehouse, and a serverless one if there is one. It starts in seconds.
- A stopped cluster takes 3 to 7 minutes to start. The launcher starts it and says so. Tell the user why the query is slow.

## When a call fails

Show the error text to the user and stop. Do not retry in a loop.

- `not logged in`, `401`, `403 Invalid access token`: run `dbx login <name>`.
- `PERMISSION_DENIED` on a table: the user lacks a grant in Unity Catalog. Tell them. Do not try other tables to get around it.
- `No SQL warehouse found`: run `dbx compute -p <name>` and ask the user which one to use.

## Never

- Ask the user to paste a token or password into chat, or print one.
- Show workspace hosts, workspace IDs, cluster IDs or local paths unless the user asks. `dbx doctor --details` prints them for a person at a terminal.
- Run `databricks` commands that change anything: jobs, clusters (except the automatic start above), permissions, tables, files.
- Write data, even when the user asks. Tell them this skill is read-only.
- Run `--debug`. It prints auth headers.
