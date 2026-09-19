from __future__ import annotations

import argparse
import http.server
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "dbx.py"

spec = importlib.util.spec_from_file_location("dbx", SCRIPT)
dbx = importlib.util.module_from_spec(spec)
sys.modules["dbx"] = dbx
spec.loader.exec_module(dbx)


# --------------------------------------------------------------------------- guard


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM a.b.c",
        "  select 1;",
        "(SELECT 1) UNION (SELECT 2)",
        "WITH x AS (SELECT 1) SELECT * FROM x",
        "SHOW CREATE TABLE a.b.c",
        "DESCRIBE TABLE EXTENDED a.b.c",
        "EXPLAIN SELECT 1",
        "SELECT 'drop table x; delete' AS s",
        "SELECT `update` FROM t -- delete everything\n",
        "SELECT delete_flag, updated_at FROM t",
    ],
)
def test_guard_allows_reads(sql):
    clean, _ = dbx.guard_read_only(sql)
    assert "--" not in clean


@pytest.mark.parametrize(
    "sql, msg",
    [
        ("DROP TABLE a.b.c", "not a read statement"),
        ("insert into t values (1)", "not a read statement"),
        ("SET x = 1", "not a read statement"),
        ("USE CATALOG prod", "not a read statement"),
        ("SELECT 1; DROP TABLE t", "one statement"),
        ("WITH x AS (SELECT 1) INSERT INTO t SELECT * FROM x", "INSERT"),
        ("/* hi */ DELETE FROM t", "not a read statement"),
        ("", "No SQL"),
        ("SELECT 'open", "unclosed"),
    ],
)
def test_guard_blocks(sql, msg):
    with pytest.raises(dbx.DbxError, match=msg):
        dbx.guard_read_only(sql)


def test_limit_added_only_when_missing():
    assert dbx.apply_limit("SELECT * FROM t", "SELECT", 50).endswith("LIMIT 50")
    assert dbx.apply_limit("SELECT * FROM t LIMIT 5", "SELECT", 50) == "SELECT * FROM t LIMIT 5"
    assert dbx.apply_limit("SELECT * FROM t limit 5 offset 10", "SELECT", 50).endswith("offset 10")
    assert dbx.apply_limit("SELECT * FROM (SELECT * FROM t LIMIT 5)", "SELECT", 50).endswith("LIMIT 50")
    assert dbx.apply_limit("SELECT 'LIMIT 5'", "SELECT", 50).endswith("LIMIT 50")
    assert dbx.apply_limit("SHOW TABLES", "SHOW", 50) == "SHOW TABLES"
    assert dbx.apply_limit("SELECT 1", "SELECT", 0) == "SELECT 1"


# --------------------------------------------------------------------------- params


def test_bind_params_quotes_and_types():
    params = [
        dbx.parse_param("name=O'Brien \\ x"),
        dbx.parse_param("n:INT=5"),
        dbx.parse_param("d:date=2026-01-02"),
        dbx.parse_param("e="),
    ]
    sql = "SELECT raw:field, x::string FROM t WHERE a = :name AND b > :n AND c >= :d AND e = :e AND f = ':name'"
    out = dbx.bind_params(sql, params)
    assert "a = 'O\\'Brien \\\\ x'" in out
    assert "b > 5" in out and "c >= DATE'2026-01-02'" in out and "e = NULL" in out
    assert "raw:field" in out and "x::string" in out and "f = ':name'" in out


@pytest.mark.parametrize("text", ["n:INT=5 OR 1=1", "d:DATE=yesterday", "b:BOOLEAN=maybe"])
def test_bind_params_rejects_bad_typed_values(text):
    p = dbx.parse_param(text)
    with pytest.raises(dbx.DbxError, match="not a valid"):
        dbx.bind_params(f"SELECT :{p.name}", [p])


def test_bind_params_rejects_unused():
    with pytest.raises(dbx.DbxError, match="not used"):
        dbx.bind_params("SELECT 1", [dbx.parse_param("x=1")])


# --------------------------------------------------------------------------- config


def test_compute_from_http_path():
    assert dbx.compute_from_http_path("/sql/1.0/warehouses/abc123") == ("warehouse", "abc123")
    assert dbx.compute_from_http_path("sql/protocolv1/o/123/0101-000000-abcd1234") == ("cluster", "0101-000000-abcd1234")
    assert dbx.compute_from_http_path("nonsense") is None


def test_normalize_host():
    assert dbx.normalize_host("adb-1.2.azuredatabricks.net/browse?o=1") == "https://adb-1.2.azuredatabricks.net?o=1"
    assert dbx.normalize_host("https://x.cloud.databricks.com/sql/editor") == "https://x.cloud.databricks.com"


def test_parse_version():
    assert dbx.parse_version("Databricks CLI v1.14.1") == (1, 14, 1)
    assert dbx.parse_version("nothing") is None


def _cfg(tmp_path, text):
    p = tmp_path / "cfg"
    p.write_text(text)
    return p


def test_resolve_profile(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABRICKS_CONFIG_PROFILE", raising=False)
    monkeypatch.setenv("DATABRICKS_CONFIG_FILE", str(_cfg(tmp_path, "[DEFAULT]\nhost = h\n[ws13]\nhost = h2\n")))
    with pytest.raises(dbx.DbxError, match="Several workspaces"):
        dbx.resolve_profile(None)
    assert dbx.resolve_profile("ws13") == "ws13"
    monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "ws13")
    assert dbx.resolve_profile(None) == "ws13"
    with pytest.raises(dbx.DbxError, match="No profile 'nope'"):
        dbx.resolve_profile("nope")


def test_default_section_does_not_leak(tmp_path, monkeypatch):
    monkeypatch.setenv("DBX_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setenv(
        "DATABRICKS_CONFIG_FILE",
        str(_cfg(tmp_path, "[DEFAULT]\nhost = h\nhttp_path = /sql/1.0/warehouses/w0\n[ws13]\nhost = h2\n")),
    )
    assert dbx.configured_compute("DEFAULT") == ("warehouse", "w0", "profile http_path")
    assert dbx.configured_compute("ws13") is None


def test_saved_choice_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("DBX_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setenv(
        "DATABRICKS_CONFIG_FILE",
        str(_cfg(tmp_path, "[ws13]\nhost = h\nhttp_path = sql/protocolv1/o/1/0101-000000-abcd\n")),
    )
    assert dbx.configured_compute("ws13")[:2] == ("cluster", "0101-000000-abcd")
    dbx.save_compute("ws13", "warehouse", "w9")
    assert dbx.configured_compute("ws13") == ("warehouse", "w9", "saved choice")


# --------------------------------------------------------------------------- end to end
# The real Databricks CLI talks to a local fake workspace. No network, no real data.


class FakeWorkspace(http.server.BaseHTTPRequestHandler):
    requests: list[tuple[str, str, dict]] = []
    cluster_state = "TERMINATED"
    status_calls = 0
    hang_warehouses = False

    def _reply(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}") if n else {}
        url = urlparse(self.path)
        cls = type(self)
        cls.requests.append((self.command, url.path.replace("/api/2.1/", "/api/2.0/"), body))
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        p = url.path.replace("/api/2.1/clusters/", "/api/2.0/clusters/")
        if p == "/api/2.0/sql/statements":
            return self._reply({
                "statement_id": "s1",
                "status": {"state": "SUCCEEDED"},
                "manifest": {"schema": {"columns": [{"name": "region", "type_name": "STRING", "position": 0}]},
                             "total_row_count": 1},
                "result": {"data_array": [["EMEA"]], "row_count": 1},
            })
        if p == "/api/2.0/clusters/get":
            assert q["cluster_id"] == "0101-000000-abcd"
            return self._reply({"cluster_id": q["cluster_id"], "state": cls.cluster_state})
        if p == "/api/2.0/clusters/start":
            cls.cluster_state = "RUNNING"
            return self._reply({})
        if p == "/api/1.2/contexts/create":
            return self._reply({"id": "ctx1"})
        if p == "/api/1.2/commands/execute":
            return self._reply({"id": "cmd1"})
        if p == "/api/1.2/commands/status":
            cls.status_calls += 1
            if cls.status_calls == 1:
                return self._reply({"id": "cmd1", "status": "Running"})
            return self._reply({
                "id": "cmd1",
                "status": "Finished",
                "results": {"resultType": "table", "schema": [{"name": "n", "type": "int"}, {"name": "ok", "type": "boolean"}],
                            "data": [[1, True], [2, None]], "truncated": False},
            })
        if p == "/api/1.2/contexts/destroy":
            return self._reply({})
        if p == "/api/2.0/preview/scim/v2/Me":
            return self._reply({"userName": "user@example.com"})
        if p == "/api/2.0/sql/warehouses" and cls.hang_warehouses:
            time.sleep(5)  # some workspaces never answer this API
        if p == "/api/2.0/sql/warehouses":
            return self._reply({"warehouses": [{"id": "w1", "name": "Shared", "state": "STOPPED",
                                                "warehouse_type": "PRO", "enable_serverless_compute": True}]})
        if p == "/api/2.0/clusters/list":
            return self._reply({"clusters": [{"cluster_id": "0101-000000-abcd", "cluster_name": "team", "state": "RUNNING", "cluster_source": "UI"},
                {"cluster_id": "0101-000000-job1", "cluster_name": "job-1-run-2", "state": "TERMINATED",
                 "cluster_source": "JOB"}]})
        return self._reply({"error_code": "NOT_FOUND", "message": p}, 404)

    do_GET = do_POST = _handle

    def log_message(self, *args):
        pass


@pytest.fixture
def workspace(tmp_path):
    if not (dbx.find_cli() and dbx.find_cli()[1] >= dbx.MIN_CLI):
        pytest.skip("Databricks CLI 1.9+ not installed")
    FakeWorkspace.requests = []
    FakeWorkspace.cluster_state = "TERMINATED"
    FakeWorkspace.status_calls = 0
    FakeWorkspace.hang_warehouses = False
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeWorkspace)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host = f"http://127.0.0.1:{server.server_address[1]}"
    cfg = tmp_path / "databrickscfg"
    cfg.write_text(
        f"[wh]\nhost = {host}\ntoken = dapiFAKE\nhttp_path = /sql/1.0/warehouses/w1\n\n"
        f"[cl]\nhost = {host}\ntoken = dapiFAKE\nhttp_path = sql/protocolv1/o/1/0101-000000-abcd\n"
    )
    env = {**os.environ, "DATABRICKS_CONFIG_FILE": str(cfg), "DBX_STATE_FILE": str(tmp_path / "s.json"),
           "DBX_POLL_SECONDS": "0.05"}
    for k in ("DATABRICKS_CONFIG_PROFILE", "DATABRICKS_WAREHOUSE_ID", "DATABRICKS_HOST", "DATABRICKS_TOKEN"):
        env.pop(k, None)
    yield env
    server.shutdown()


def run(env, *args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=120)


def test_e2e_warehouse_query_with_params(workspace):
    r = run(workspace, "sql", "-p", "wh", "SELECT region FROM s.t WHERE region = :r", "--param", "r=EMEA")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == [{"region": "EMEA"}]
    (_, _, body), = [x for x in FakeWorkspace.requests if x[1] == "/api/2.0/sql/statements"]
    assert body["warehouse_id"] == "w1"
    assert body["statement"].endswith("LIMIT 1000")
    assert body["parameters"] == [{"name": "r", "value": "EMEA"}]


def test_e2e_cluster_starts_and_returns_rows(workspace):
    r = run(workspace, "sql", "-p", "cl", "SELECT n, ok FROM s.t WHERE n > :n", "--param", "n:INT=0", "--limit", "5")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == [{"n": "1", "ok": "true"}, {"n": "2", "ok": None}]
    assert "Starting it" in r.stderr
    paths = [p for _, p, _ in FakeWorkspace.requests]
    assert "/api/2.0/clusters/start" in paths
    assert paths[-1] == "/api/1.2/contexts/destroy"
    (_, _, body), = [x for x in FakeWorkspace.requests if x[1] == "/api/1.2/commands/execute"]
    assert body["command"] == "SELECT n, ok FROM s.t WHERE n > 0\nLIMIT 5"


def test_e2e_cluster_csv(workspace):
    FakeWorkspace.cluster_state = "RUNNING"
    r = run(workspace, "sql", "-p", "cl", "SELECT n, ok FROM s.t", "--csv")
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == ["n,ok", "1,true", "2,"]


def test_e2e_blocked_sql_sends_nothing(workspace):
    r = run(workspace, "sql", "-p", "wh", "DELETE FROM s.t")
    assert r.returncode == 2
    assert "Blocked" in r.stderr
    assert FakeWorkspace.requests == []


def test_e2e_saved_compute_overrides_profile(workspace):
    assert run(workspace, "compute", "-p", "cl", "--use", "warehouse:w7").returncode == 0
    r = run(workspace, "sql", "-p", "cl", "SELECT 1")
    assert r.returncode == 0, r.stderr
    (_, _, body), = [x for x in FakeWorkspace.requests if x[1] == "/api/2.0/sql/statements"]
    assert body["warehouse_id"] == "w7"


def test_e2e_doctor_hides_hosts_and_ids_by_default(workspace):
    r = run(workspace, "doctor")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "wh" in r.stdout and "compute: warehouse" in r.stdout and "compute: cluster" in r.stdout
    assert "127.0.0.1" not in r.stdout and "0101-000000-abcd" not in r.stdout and " at " not in r.stdout


def test_e2e_doctor_details_shows_hosts_and_ids(workspace):
    r = run(workspace, "doctor", "--details")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "127.0.0.1" in r.stdout and "warehouse w1" in r.stdout and "cluster 0101-000000-abcd" in r.stdout


def test_cli_found_in_winget_packages_folder(tmp_path, monkeypatch):
    # With symlinks off, winget installs here and only updates the PATH saved in the registry.
    pkg = tmp_path / "Microsoft" / "WinGet" / "Packages" / "Databricks.DatabricksCLI_Microsoft.Winget.Source_x"
    pkg.mkdir(parents=True)
    exe = pkg / "databricks.exe"
    exe.write_text("#!/bin/sh\necho 'Databricks CLI v9.9.9'\n")
    exe.chmod(0o755)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("DATABRICKS_CLI", raising=False)
    assert str(exe) in dbx.cli_candidates()
    if os.name != "nt":  # the fake CLI is a shell script, which Windows cannot run as an .exe
        assert dbx.cli_version(str(exe)) == (9, 9, 9)


def test_e2e_doctor_with_no_workspaces_tells_agent_to_ask(workspace, tmp_path):
    env = {**workspace, "DATABRICKS_CONFIG_FILE": str(tmp_path / "missing")}
    r = run(env, "doctor")
    assert r.returncode == 1
    assert "dbx login <workspace-url>" in r.stdout


def test_e2e_compute_lists_choices(workspace):
    r = run(workspace, "compute", "-p", "wh")
    assert r.returncode == 0, r.stderr
    assert "warehouse:w1" in r.stdout and "serverless" in r.stdout
    assert "cluster:0101-000000-abcd" in r.stdout
    assert "job1" not in r.stdout and "1 job and pipeline clusters not shown" in r.stdout


TRICKY_SQL = """SELECT region FROM s.t
WHERE name LIKE '%PATH%' AND note = "a \\"quote\\"" AND cost = '$5 & up' -- comment
"""


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16"])
def test_e2e_sql_file_any_encoding(workspace, tmp_path, encoding):
    # utf-16 is what Windows PowerShell 5.1 writes with Out-File and >.
    f = tmp_path / "q.sql"
    f.write_text(TRICKY_SQL, encoding=encoding)
    r = run(workspace, "sql", "-p", "wh", "--file", str(f))
    assert r.returncode == 0, r.stderr
    (_, _, body), = [x for x in FakeWorkspace.requests if x[1] == "/api/2.0/sql/statements"]
    assert "LIKE '%PATH%'" in body["statement"]
    assert '"a \\"quote\\""' in body["statement"]
    assert "'$5 & up'" in body["statement"]
    assert "comment" not in body["statement"]


@pytest.mark.skipif(os.name != "nt", reason="Windows launcher")
def test_windows_cmd_launcher_end_to_end(workspace, tmp_path):
    f = tmp_path / "q.sql"
    f.write_text(TRICKY_SQL, encoding="utf-16")
    r = subprocess.run(
        ["cmd", "/c", str(SKILL / "scripts" / "dbx.cmd"), "sql", "-p", "wh", "--file", str(f)],
        env=workspace, capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout) == [{"region": "EMEA"}]
    (_, _, body), = [x for x in FakeWorkspace.requests if x[1] == "/api/2.0/sql/statements"]
    assert "LIKE '%PATH%'" in body["statement"]


@pytest.mark.skipif(os.name != "nt", reason="Windows launcher")
def test_windows_cmd_launcher_exit_code():
    r = subprocess.run(["cmd", "/c", str(SKILL / "scripts" / "dbx.cmd"), "sql", "DROP TABLE t"],
                       capture_output=True, text=True)
    assert r.returncode == 2 and "Blocked" in r.stderr


def test_cmd_launcher_has_crlf_and_no_unix_paths():
    text = (SKILL / "scripts" / "dbx.cmd").read_bytes()
    assert b"\r\n" in text
    assert b"/dev/null" not in text  # cmd.exe cannot open it, so the Python check always fails


@pytest.mark.skipif(not shutil.which("sh"), reason="needs sh")
def test_unix_launcher_runs():
    r = subprocess.run(["sh", str(SKILL / "scripts" / "dbx"), "--help"], capture_output=True, text=True)
    assert r.returncode == 0 and "doctor" in r.stdout


def test_e2e_open_stdin_does_not_hang(workspace):
    # An MCP stdio server starts tools with a stdin pipe that stays open and silent.
    FakeWorkspace.cluster_state = "RUNNING"
    p = subprocess.Popen([sys.executable, str(SCRIPT), "sql", "-p", "cl", "SELECT 1"], env=workspace,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert p.wait(timeout=60) == 0, p.stderr.read()
    except subprocess.TimeoutExpired:
        p.kill()
        pytest.fail("dbx hung while its stdin stayed open")
    finally:
        p.stdin.close()
    assert json.loads(p.stdout.read())


def test_compute_lists_clusters_when_warehouse_api_hangs(workspace, monkeypatch, capsys):
    for k, v in workspace.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(dbx, "WAREHOUSE_API_TIMEOUT_S", 1)
    FakeWorkspace.hang_warehouses = True
    assert dbx.cmd_compute(argparse.Namespace(profile="wh", use=None)) == 0
    out = capsys.readouterr().out
    assert "did not answer" in out and "cluster:0101-000000-abcd" in out


def test_timeout_message_hides_the_cli_path(monkeypatch, capsys):
    def boom(a):
        raise subprocess.TimeoutExpired([r"C:\Users\someone\AppData\databricks.exe", "warehouses", "list"], 120)

    monkeypatch.setattr(dbx, "cmd_doctor", boom)
    assert dbx.main(["doctor"]) == 2
    err = capsys.readouterr().err
    assert "timed out after 120 s: databricks warehouses list" in err and "someone" not in err
