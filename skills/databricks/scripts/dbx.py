#!/usr/bin/env python3
"""Read-only Databricks access for coding agents, on top of the Databricks CLI.

Standard library only. The Databricks CLI does auth, HTTP and SQL warehouse
polling. This script adds what the CLI does not have:

- a read-only guard and a default row limit
- a choice of compute per profile (SQL warehouse, or an all-purpose cluster)
- SQL on all-purpose clusters (Command Execution API 1.2), same JSON shape
- CLI install and upgrade, and a login flow for new users

Run `dbx.py --help` for the commands.
"""

from __future__ import annotations

import argparse
import configparser
import csv
import functools
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MIN_CLI = (1, 9, 0)  # `genie ask` and `experimental aitools tools query`
DEFAULT_LIMIT = 1000
CLUSTER_START_TIMEOUT_S = 15 * 60
COMMAND_TIMEOUT_S = 10 * 60
# DBX_POLL_SECONDS exists for tests. Real runs use the defaults.
POLL_S = float(os.environ.get("DBX_POLL_SECONDS", 3))
CLUSTER_POLL_S = float(os.environ.get("DBX_POLL_SECONDS", 10))

READ_START = {"SELECT", "WITH", "VALUES", "SHOW", "DESCRIBE", "DESC", "EXPLAIN"}
# Only checked for SELECT / WITH / VALUES. SHOW, DESCRIBE and EXPLAIN never write.
WRITE_WORDS = {
    "INSERT", "UPDATE", "DELETE", "MERGE", "DROP", "CREATE", "ALTER", "TRUNCATE",
    "GRANT", "REVOKE", "COPY", "OPTIMIZE", "VACUUM", "REFRESH", "RESTORE", "MSCK",
    "REPAIR", "CACHE", "UNCACHE", "CALL", "REPLACE",
}
LIMITABLE = {"SELECT", "WITH", "VALUES"}


class DbxError(Exception):
    """A failure the agent should show to the user as is."""


# --------------------------------------------------------------------------- SQL


@dataclass
class Scan:
    statements: list[str]  # comments removed, string literals kept
    masked: list[str]  # same, but literal and quoted-identifier contents blanked


def scan_sql(sql: str) -> Scan:
    """Split on top-level `;` and drop comments, respecting quotes."""
    statements: list[str] = []
    masked: list[str] = []
    out: list[str] = []
    mask: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if c == "-" and nxt == "-":
            j = sql.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and nxt == "*":
            j = sql.find("*/", i + 2)
            if j < 0:
                raise DbxError("SQL has an unclosed /* comment.")
            out.append(" ")
            mask.append(" ")
            i = j + 2
            continue
        if c in "'\"`":
            j = i + 1
            while j < n:
                if sql[j] == "\\" and c != "`":
                    j += 2
                    continue
                if sql[j] == c:
                    if c == "`" and j + 1 < n and sql[j + 1] == "`":
                        j += 2
                        continue
                    break
                j += 1
            if j >= n:
                raise DbxError(f"SQL has an unclosed {c} quote.")
            out.append(sql[i : j + 1])
            mask.append(c + " " * (j - i - 1) + c)
            i = j + 1
            continue
        if c == ";":
            statements.append("".join(out).strip())
            masked.append("".join(mask).strip())
            out, mask = [], []
            i += 1
            continue
        out.append(c)
        mask.append(c)
        i += 1
    statements.append("".join(out).strip())
    masked.append("".join(mask).strip())
    pairs = [(s, m) for s, m in zip(statements, masked) if s]
    return Scan([s for s, _ in pairs], [m for _, m in pairs])


def first_keyword(masked: str) -> str:
    m = re.match(r"[\s(]*([A-Za-z]+)", masked)
    return m.group(1).upper() if m else ""


def guard_read_only(sql: str) -> tuple[str, str]:
    """Return (clean_sql, first_keyword) or raise DbxError."""
    scan = scan_sql(sql)
    if not scan.statements:
        raise DbxError("No SQL statement given.")
    if len(scan.statements) > 1:
        raise DbxError("Send one statement at a time. This skill does not run SQL scripts.")
    clean, masked = scan.statements[0], scan.masked[0]
    kw = first_keyword(masked)
    if kw not in READ_START:
        raise DbxError(
            f"Blocked: '{kw or '?'}' is not a read statement. This skill runs only "
            "SELECT, WITH, VALUES, SHOW, DESCRIBE and EXPLAIN."
        )
    if kw in LIMITABLE:
        words = {w.upper() for w in re.findall(r"[A-Za-z_]+", masked)}
        bad = sorted(words & WRITE_WORDS)
        if bad:
            raise DbxError(f"Blocked: the statement contains write keyword(s): {', '.join(bad)}.")
    return clean, kw


def apply_limit(sql: str, kw: str, limit: int) -> str:
    if limit <= 0 or kw not in LIMITABLE:
        return sql
    masked = scan_sql(sql).masked[0]
    if re.search(r"\bLIMIT\s+(\d+|ALL)(\s+OFFSET\s+\d+)?\s*$", masked, re.IGNORECASE):
        return sql
    return f"{sql}\nLIMIT {limit}"


PARAM_RE = re.compile(r"^([A-Za-z_]\w*)(?::([A-Za-z]+))?=(.*)$", re.DOTALL)
INT_TYPES = {"INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "LONG", "SHORT", "BYTE"}
NUM_TYPES = {"DOUBLE", "FLOAT", "DECIMAL", "REAL"}


@dataclass
class Param:
    name: str
    type: str  # upper case, "STRING" by default
    value: str

    def cli_arg(self) -> str:
        if self.type == "STRING":
            return f"{self.name}={self.value}"
        return f"{self.name}:{self.type}={self.value}"


def parse_param(text: str) -> Param:
    m = PARAM_RE.match(text)
    if not m:
        raise DbxError(f"Bad --param '{text}'. Use name=value or name:TYPE=value.")
    return Param(m.group(1), (m.group(2) or "STRING").upper(), m.group(3))


def sql_literal(p: Param) -> str:
    v = p.value
    if v == "":
        return "NULL"
    t = p.type
    if t == "STRING":
        return "'" + v.replace("\\", "\\\\").replace("'", "\\'") + "'"
    if t in INT_TYPES and re.fullmatch(r"-?\d+", v):
        return v
    if t in NUM_TYPES and re.fullmatch(r"-?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?", v):
        return v
    if t == "BOOLEAN" and v.lower() in {"true", "false"}:
        return v.lower()
    if t == "DATE" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return f"DATE'{v}'"
    if t == "TIMESTAMP" and re.fullmatch(r"\d{4}-\d{2}-\d{2}([ T][\d:.]+)?", v):
        return f"TIMESTAMP'{v}'"
    raise DbxError(f"Parameter '{p.name}': value '{v}' is not a valid {t}.")


def bind_params(sql: str, params: list[Param]) -> str:
    """Put literals in place of `:name` markers (for clusters, which have no bind API).

    Only names given with --param are replaced. `col:path` JSON access and `::` casts stay.
    """
    if not params:
        return sql
    by_name = {p.name: p for p in params}
    masked = scan_sql(sql).masked[0]
    used: set[str] = set()
    parts: list[str] = []
    last = 0
    for m in re.finditer(r"(?<![:\w]):([A-Za-z_]\w*)", masked):
        name = m.group(1)
        if name not in by_name:
            continue
        parts.append(sql[last : m.start()])
        parts.append(sql_literal(by_name[name]))
        last = m.end()
        used.add(name)
    parts.append(sql[last:])
    unused = sorted(set(by_name) - used)
    if unused:
        raise DbxError(f"Parameter(s) not used in the SQL: {', '.join(unused)}.")
    return "".join(parts)


# --------------------------------------------------------------------------- CLI


def parse_version(text: str) -> tuple[int, ...] | None:
    m = re.search(r"v?(\d+)\.(\d+)\.(\d+)", text)
    return tuple(int(x) for x in m.groups()) if m else None


def cli_candidates() -> list[str]:
    found: list[str] = []
    env = os.environ.get("DATABRICKS_CLI")
    if env:
        found.append(env)
    on_path = shutil.which("databricks")
    if on_path:
        found.append(on_path)
    home = Path.home()
    extra = [
        home / ".local" / "bin" / "databricks",
        Path("/opt/homebrew/bin/databricks"),
        Path("/usr/local/bin/databricks"),
    ]
    # winget puts new CLIs here. The current process PATH does not see them until a new terminal.
    local = os.environ.get("LOCALAPPDATA")
    if local:
        extra.append(Path(local) / "Microsoft" / "WinGet" / "Links" / "databricks.exe")
    program_files = os.environ.get("ProgramFiles")
    if program_files:
        extra.append(Path(program_files) / "WinGet" / "Links" / "databricks.exe")
    found += [str(p) for p in extra if p.is_file()]
    seen: set[str] = set()
    return [p for p in found if not (p in seen or seen.add(p))]


def cli_version(path: str) -> tuple[int, ...] | None:
    try:
        r = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=30, **NO_STDIN)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_version(r.stdout + r.stderr)


@functools.cache
def find_cli() -> tuple[str, tuple[int, ...]] | None:
    """The newest Databricks CLI on this machine. A stale copy can sit earlier on PATH."""
    best: tuple[str, tuple[int, ...]] | None = None
    for path in cli_candidates():
        v = cli_version(path)
        if v and (best is None or v > best[1]):
            best = (path, v)
    return best


def fmt_version(v: tuple[int, ...]) -> str:
    return ".".join(map(str, v))


def require_cli() -> str:
    found = find_cli()
    if not found:
        raise DbxError("Databricks CLI not found. Run: dbx doctor --fix")
    path, v = found
    if v < MIN_CLI:
        raise DbxError(
            f"Databricks CLI {fmt_version(v)} is too old (need {fmt_version(MIN_CLI)}+). "
            "Run: dbx doctor --fix"
        )
    return path


# Child processes never read our stdin. Under an MCP stdio server, stdin carries the
# protocol, and a child that inherits it can hang the server.
NO_STDIN = {"stdin": subprocess.DEVNULL}


def run_cli(args: list[str], *, stdin: str | None = None, timeout: float | None = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [require_cli(), *args],
        **({"input": stdin} if stdin is not None else NO_STDIN),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def cli_json(args: list[str], *, timeout: float | None = 120) -> Any:
    r = run_cli([*args, "-o", "json"], timeout=timeout)
    if r.returncode != 0:
        raise DbxError((r.stderr or r.stdout).strip() or f"databricks {' '.join(args)} failed")
    out = r.stdout.strip()
    return json.loads(out) if out else None


def api(method: str, path: str, profile: str, body: dict[str, Any] | None = None) -> Any:
    args = ["api", method, path, "-p", profile]
    if body is not None:
        args += ["--json", json.dumps(body)]
    return cli_json(args)


def install_cli(upgrade: bool) -> None:
    """Install or upgrade the CLI. The caller checks the version after, not the exit code.

    winget: `install` also upgrades a winget copy. An old copy from a zip stays and
    find_cli() picks the newer one. winget exit codes for "already current" are not zero.
    """
    if platform.system() == "Windows":
        if not shutil.which("winget"):
            raise DbxError(
                "winget is not on this machine. Ask IT to install 'Databricks CLI' "
                "(winget id Databricks.DatabricksCLI), or install 'App Installer' from the Microsoft Store."
            )
        cmds = [[
            "winget", "install", "--id", "Databricks.DatabricksCLI", "--exact", "--source", "winget",
            "--silent", "--accept-source-agreements", "--accept-package-agreements",
        ]]
    elif shutil.which("brew"):
        cmds = [["brew", "tap", "databricks/tap"], ["brew", "upgrade" if upgrade else "install", "databricks"]]
    else:
        raise DbxError(
            "No Homebrew found. Install the Databricks CLI by hand: "
            "https://docs.databricks.com/dev-tools/cli/install.html"
        )
    for cmd in cmds:
        print("Running:", " ".join(cmd), file=sys.stderr)
        subprocess.run(cmd, check=False, **NO_STDIN)
    find_cli.cache_clear()


# --------------------------------------------------------------------------- profiles


def config_path() -> Path:
    return Path(os.environ.get("DATABRICKS_CONFIG_FILE") or Path.home() / ".databrickscfg")


def read_config() -> configparser.ConfigParser:
    # [DEFAULT] is an ordinary profile in .databrickscfg, so it must not inherit.
    cp = configparser.ConfigParser(default_section="__dbx_no_default__", interpolation=None, strict=False)
    path = config_path()
    if path.is_file():
        cp.read(path, encoding="utf-8")
    return cp


def profile_names(cp: configparser.ConfigParser) -> list[str]:
    return [s for s in cp.sections() if s != "__settings__"]


def resolve_profile(name: str | None) -> str:
    cp = read_config()
    names = profile_names(cp)
    if name:
        if name not in names:
            raise DbxError(
                f"No profile '{name}' in {config_path()}. Known: {', '.join(names) or 'none'}. "
                "Run: dbx login <workspace-url> --name <name>"
            )
        return name
    env = os.environ.get("DATABRICKS_CONFIG_PROFILE")
    if env in names:
        return env
    default = cp.get("__settings__", "default_profile", fallback=None)
    if default in names:
        return default
    if len(names) == 1:
        return names[0]
    if not names:
        raise DbxError("No Databricks workspace is set up. Run: dbx doctor")
    raise DbxError(f"Several workspaces are set up. Pass -p with one of: {', '.join(names)}")


# --------------------------------------------------------------------------- compute


def state_path() -> Path:
    return Path(os.environ.get("DBX_STATE_FILE") or Path.home() / ".config" / "databricks-skill" / "compute.json")


def load_state() -> dict[str, dict[str, str]]:
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_compute(profile: str, kind: str, cid: str) -> None:
    data = load_state()
    data[profile] = {"kind": kind, "id": cid}
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def compute_from_http_path(http_path: str) -> tuple[str, str] | None:
    m = re.search(r"/warehouses/([0-9A-Za-z]+)", http_path)
    if m:
        return "warehouse", m.group(1)
    m = re.search(r"protocolv1/o/\d+/([0-9A-Za-z-]+)", http_path)
    if m:
        return "cluster", m.group(1)
    return None


def configured_compute(profile: str) -> tuple[str, str, str] | None:
    """(kind, id, source) from saved choice or the profile, without network calls."""
    saved = load_state().get(profile)
    if saved and saved.get("kind") in {"warehouse", "cluster"} and saved.get("id"):
        return saved["kind"], saved["id"], "saved choice"
    cp = read_config()
    if cp.has_section(profile):
        sec = cp[profile]
        if sec.get("warehouse_id"):
            return "warehouse", sec["warehouse_id"], "profile warehouse_id"
        if sec.get("http_path"):
            hit = compute_from_http_path(sec["http_path"])
            if hit:
                return hit[0], hit[1], "profile http_path"
        if sec.get("cluster_id"):
            return "cluster", sec["cluster_id"], "profile cluster_id"
    return None


def resolve_compute(profile: str, warehouse: str | None, cluster: str | None) -> tuple[str, str]:
    if warehouse:
        return "warehouse", warehouse
    if cluster:
        return "cluster", cluster
    hit = configured_compute(profile)
    if hit:
        return hit[0], hit[1]
    env = os.environ.get("DATABRICKS_WAREHOUSE_ID")
    if env:
        return "warehouse", env
    try:
        info = cli_json(["experimental", "aitools", "tools", "get-default-warehouse", "-p", profile])
    except DbxError as e:
        raise DbxError(
            f"No SQL warehouse found for '{profile}' ({e}). "
            f"Run: dbx compute -p {profile}  and pick a warehouse or cluster."
        ) from None
    return "warehouse", info["id"]


# --------------------------------------------------------------------------- execution


def run_on_warehouse(profile: str, wid: str, sql: str, params: list[Param], fmt: str) -> int:
    args = ["experimental", "aitools", "tools", "query", "-p", profile, "--warehouse", wid, "--output", fmt]
    for p in params:
        args += ["--param", p.cli_arg()]
    # SQL goes on stdin: no quoting problems on Windows command lines.
    r = run_cli(args, stdin=sql, timeout=COMMAND_TIMEOUT_S + CLUSTER_START_TIMEOUT_S)
    sys.stdout.write(r.stdout)
    if r.stderr:
        sys.stderr.write(r.stderr)
    return r.returncode


def ensure_cluster_running(profile: str, cid: str) -> None:
    deadline = time.monotonic() + CLUSTER_START_TIMEOUT_S
    started = False
    while True:
        state = cli_json(["clusters", "get", cid, "-p", profile]).get("state", "")
        if state == "RUNNING":
            return
        if state == "TERMINATED" and not started:
            print(f"Cluster {cid} is stopped. Starting it. This takes 3 to 7 minutes.", file=sys.stderr)
            r = run_cli(["clusters", "start", cid, "--no-wait", "-p", profile])
            if r.returncode != 0:
                raise DbxError(f"Cannot start cluster {cid}: {(r.stderr or r.stdout).strip()}")
            started = True
        elif state in {"ERROR", "UNKNOWN"}:
            raise DbxError(f"Cluster {cid} is in state {state}.")
        if time.monotonic() > deadline:
            raise DbxError(f"Cluster {cid} did not reach RUNNING in {CLUSTER_START_TIMEOUT_S // 60} minutes.")
        time.sleep(CLUSTER_POLL_S)


def run_on_cluster(profile: str, cid: str, sql: str, params: list[Param], fmt: str) -> int:
    sql = bind_params(sql, params)
    ensure_cluster_running(profile, cid)
    ctx = api("post", "/api/1.2/contexts/create", profile, {"clusterId": cid, "language": "sql"})["id"]
    try:
        cmd = api(
            "post", "/api/1.2/commands/execute", profile,
            {"clusterId": cid, "contextId": ctx, "language": "sql", "command": sql},
        )["id"]
        status_path = f"/api/1.2/commands/status?clusterId={cid}&contextId={ctx}&commandId={cmd}"
        deadline = time.monotonic() + COMMAND_TIMEOUT_S
        while True:
            st = api("get", status_path, profile)
            if st.get("status") in {"Finished", "Error", "Cancelled"}:
                break
            if time.monotonic() > deadline:
                api("post", "/api/1.2/commands/cancel", profile, {"clusterId": cid, "contextId": ctx, "commandId": cmd})
                raise DbxError(f"Query took longer than {COMMAND_TIMEOUT_S // 60} minutes. Cancelled it.")
            time.sleep(POLL_S)
    finally:
        try:
            api("post", "/api/1.2/contexts/destroy", profile, {"clusterId": cid, "contextId": ctx})
        except DbxError:
            pass
    return render_cluster_result(st, fmt)


def _cell(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    return str(v)


def render_cluster_result(st: dict[str, Any], fmt: str) -> int:
    res = st.get("results") or {}
    rtype = res.get("resultType")
    if st.get("status") != "Finished" or rtype == "error":
        msg = res.get("summary") or res.get("cause") or st.get("status")
        raise DbxError(f"Query failed: {msg}")
    if rtype != "table":
        print(res.get("data", ""))
        return 0
    cols = [c["name"] for c in res.get("schema", [])]
    rows = [[_cell(v) for v in row] for row in res.get("data", [])]
    if fmt == "csv":
        w = csv.writer(sys.stdout, lineterminator="\n")
        w.writerow(cols)
        w.writerows(rows)
    else:
        print(json.dumps([dict(zip(cols, r)) for r in rows], indent=2))
    note = f"{len(rows)} rows"
    if res.get("truncated"):
        note += " (the cluster cut the result; add a narrower filter or LIMIT)"
    print(note, file=sys.stderr)
    return 0


# --------------------------------------------------------------------------- commands


def cmd_doctor(a: argparse.Namespace) -> int:
    found = find_cli()
    if not found or found[1] < MIN_CLI:
        if not a.fix:
            have = fmt_version(found[1]) if found else "not installed"
            print(f"CLI: {have} (need {fmt_version(MIN_CLI)}+). Next: dbx doctor --fix")
            return 1
        install_cli(upgrade=found is not None)
        found = find_cli()
        if not found or found[1] < MIN_CLI:
            raise DbxError(
                "The install did not give a CLI "
                f"{fmt_version(MIN_CLI)}+. See the installer output above. If IT blocks it, ask them to "
                "install 'Databricks CLI'."
            )
    print(f"CLI: {fmt_version(found[1])} at {found[0]}")

    names = profile_names(read_config())
    if not names:
        print("Workspaces: none set up.")
        print("Next: ask the user for their workspace URL (copy it from the browser), then run:")
        print("  dbx login <workspace-url> --name <short-name>")
        return 1
    listing = cli_json(["auth", "profiles"], timeout=180) or {}
    bad = 0
    print("Workspaces:")
    for p in listing.get("profiles", []):
        comp = configured_compute(p["name"])
        comp_txt = f"{comp[0]} {comp[1]}" if comp else "auto (default SQL warehouse)"
        ok = "OK" if p.get("valid") else "NOT LOGGED IN"
        bad += 0 if p.get("valid") else 1
        print(f"  {p['name']:<16} {ok:<14} {p.get('host', '')}  compute: {comp_txt}")
    if bad:
        print("Next: for each NOT LOGGED IN workspace, run: dbx login <name>")
    return 1 if bad else 0


def normalize_host(url: str) -> str:
    url = url.strip()
    if not re.match(r"https?://", url):
        url = "https://" + url
    m = re.match(r"(https?://[^/?#]+)[^?#]*(\?[^#]*)?", url)
    host, query = m.group(1), m.group(2) or ""
    wid = re.search(r"[?&](o|w|workspace_id)=(\d+)", query)
    return f"{host}?o={wid.group(2)}" if wid else host


def cmd_login(a: argparse.Namespace) -> int:
    cli = require_cli()
    cp = read_config()
    target = a.target
    if target in profile_names(cp):
        name, host = target, cp[target].get("host", "")
    else:
        host = normalize_host(target)
        name = a.name or re.sub(r"[^A-Za-z0-9_-]", "-", host.split("//", 1)[1].split(".", 1)[0])
    if not host:
        raise DbxError(f"Profile '{name}' has no host. Pass the workspace URL instead.")
    if a.pat:
        print("The user runs this in their own terminal. It asks for the token. Never paste a token into chat.")
        print(f'  PowerShell:     & "{cli}" configure --host "{host}" --profile {name}')
        print(f'  Command Prompt: "{cli}" configure --host "{host}" --profile {name}')
        return 0
    print(f"Opening the browser to sign in to {host} as profile '{name}'...", file=sys.stderr)
    r = subprocess.run([cli, "auth", "login", "--host", host, "--profile", name, "--timeout", "10m"], check=False, **NO_STDIN)
    if r.returncode != 0:
        print(
            "Browser login failed. If your company turned off OAuth for the CLI, use a token:\n"
            f"  dbx login {target} --name {name} --pat",
            file=sys.stderr,
        )
        return r.returncode
    me = cli_json(["current-user", "me", "-p", name])
    print(f"Signed in to '{name}' as {me.get('userName')}.")
    return 0


def cmd_compute(a: argparse.Namespace) -> int:
    profile = resolve_profile(a.profile)
    if a.use:
        m = re.fullmatch(r"(warehouse|cluster):([0-9A-Za-z-]+)", a.use)
        if not m:
            raise DbxError("Use --use warehouse:<id> or --use cluster:<id>.")
        save_compute(profile, m.group(1), m.group(2))
        print(f"'{profile}' now uses {m.group(1)} {m.group(2)}.")
        return 0
    cur = configured_compute(profile)
    print(f"Current: {f'{cur[0]} {cur[1]} ({cur[2]})' if cur else 'auto (default SQL warehouse)'}")
    print("SQL warehouses (preferred for SQL):")
    for w in cli_json(["warehouses", "list", "-p", profile]) or []:
        kind = "serverless" if w.get("enable_serverless_compute") else w.get("warehouse_type", "").lower()
        print(f"  warehouse:{w['id']:<20} {w.get('state', ''):<10} {kind:<10} {w.get('name', '')}")
    print("Clusters:")
    for c in cli_json(["clusters", "list", "-p", profile]) or []:
        print(f"  cluster:{c['cluster_id']:<22} {c.get('state', ''):<10} {c.get('cluster_name', '')}")
    print(f"Save a choice with: dbx compute -p {profile} --use warehouse:<id>")
    return 0


def read_text_any(path: str) -> str:
    """UTF-8 with or without BOM, or UTF-16 (the Windows PowerShell 5.1 default for Out-File and >)."""
    data = Path(path).read_bytes()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    return data.decode("utf-8-sig")


def read_sql_arg(a: argparse.Namespace) -> str:
    if a.file:
        return read_text_any(a.file)
    if a.sql and a.sql != "-":
        return a.sql
    return sys.stdin.read()


def cmd_sql(a: argparse.Namespace) -> int:
    clean, kw = guard_read_only(read_sql_arg(a))
    sql = apply_limit(clean, kw, a.limit)
    params = [parse_param(p) for p in a.param]
    profile = resolve_profile(a.profile)
    kind, cid = resolve_compute(profile, a.warehouse, a.cluster)
    fmt = "csv" if a.csv else "json"
    if kind == "warehouse":
        return run_on_warehouse(profile, cid, sql, params, fmt)
    return run_on_cluster(profile, cid, sql, params, fmt)


def cmd_schema(a: argparse.Namespace) -> int:
    for t in a.tables:
        if not re.fullmatch(r"[\w`.-]+", t):
            raise DbxError(f"Bad table name '{t}'. Use catalog.schema.table.")
    profile = resolve_profile(a.profile)
    kind, cid = resolve_compute(profile, a.warehouse, a.cluster)
    if kind == "warehouse":
        r = run_cli(["experimental", "aitools", "tools", "discover-schema", *a.tables, "-p", profile],
                    timeout=COMMAND_TIMEOUT_S)
        sys.stdout.write(r.stdout)
        sys.stderr.write(r.stderr)
        return r.returncode
    for t in a.tables:
        print(f"## {t}")
        run_on_cluster(profile, cid, f"DESCRIBE TABLE EXTENDED {t}", [], "json")
    return 0


def cmd_ask(a: argparse.Namespace) -> int:
    profile = resolve_profile(a.profile)
    args = [require_cli(), "genie", "ask", a.question, "-p", profile, "--include-sql"]
    if not sys.stdout.isatty():
        args += ["-o", "json"]  # a tool reads this, not a person
    if a.session:
        args += ["-s", a.session]
    return subprocess.run(args, check=False, **NO_STDIN).returncode


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="dbx", description="Read-only Databricks access for coding agents.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("doctor", help="Check the CLI and every workspace login.")
    p.add_argument("--fix", action="store_true", help="Install or upgrade the Databricks CLI.")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("login", help="Sign in to a workspace in the browser (OAuth).")
    p.add_argument("target", help="Workspace URL, or an existing profile name.")
    p.add_argument("--name", help="Profile name to save, e.g. ws13.")
    p.add_argument("--pat", action="store_true", help="Print the token setup command instead of OAuth.")
    p.set_defaults(fn=cmd_login)

    def with_compute(p: argparse.ArgumentParser) -> None:
        p.add_argument("-p", "--profile")
        g = p.add_mutually_exclusive_group()
        g.add_argument("--warehouse", help="SQL warehouse ID for this call.")
        g.add_argument("--cluster", help="Cluster ID for this call.")

    p = sub.add_parser("compute", help="List warehouses and clusters, or save a choice.")
    p.add_argument("-p", "--profile")
    p.add_argument("--use", help="warehouse:<id> or cluster:<id>")
    p.set_defaults(fn=cmd_compute)

    p = sub.add_parser("sql", help="Run one read-only SQL statement.")
    p.add_argument("sql", nargs="?", help="SQL text, or '-' for stdin.")
    p.add_argument("-f", "--file", help="Read the SQL from a file.")
    p.add_argument("--param", action="append", default=[], help="name=value or name:TYPE=value")
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"Row limit (default {DEFAULT_LIMIT}, 0 = none).")
    p.add_argument("--csv", action="store_true", help="CSV instead of JSON.")
    with_compute(p)
    p.set_defaults(fn=cmd_sql)

    p = sub.add_parser("schema", help="Columns, types and sample rows for tables.")
    p.add_argument("tables", nargs="+", help="catalog.schema.table")
    with_compute(p)
    p.set_defaults(fn=cmd_schema)

    p = sub.add_parser("ask", help="Ask Genie a question in plain English.")
    p.add_argument("question")
    p.add_argument("-p", "--profile")
    p.add_argument("-s", "--session", help="Reuse a label to ask follow-up questions.")
    p.set_defaults(fn=cmd_ask)
    return ap


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    a = build_parser().parse_args(argv)
    try:
        return a.fn(a)
    except DbxError as e:
        print(f"dbx: {e}", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired as e:
        print(f"dbx: timed out after {int(e.timeout)} s: {' '.join(map(str, e.cmd[:3]))}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
