"""The MCP adapter, loaded the same way the work repo's FastMCP server loads tool scripts."""

import asyncio
import sys
import types
from functools import wraps
from pathlib import Path

import pytest

fastmcp = pytest.importorskip("fastmcp")
from fastmcp import Client, FastMCP  # noqa: E402
from fastmcp.tools.function_tool import FunctionTool  # noqa: E402

from test_dbx import FakeWorkspace, workspace  # noqa: E402,F401  (fixture)

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dbx_mcp.py"
TOOLS = ["databricks_setup", "databricks_sql", "databricks_schema", "databricks_ask", "databricks_compute"]


def load_like_server(script: Path, function_name: str):
    """Same steps as create_tool_function in the work repo's mcp/server.py."""
    sys.modules["dynamic_module"] = types.ModuleType("dynamic_module")
    namespace = {"__file__": str(script), "__name__": "dynamic_module", "__package__": None, "sys": sys}
    sys.path.insert(0, str(script.parent))
    exec(script.read_text(encoding="utf-8"), namespace)
    func = namespace[function_name]

    @wraps(func)
    def wrapped(*args, **kwargs):
        return func(*args, **kwargs)

    return wrapped


@pytest.fixture
def server(workspace, monkeypatch):
    for k, v in workspace.items():
        monkeypatch.setenv(k, v)
    mcp = FastMCP("test")
    for name in TOOLS:
        mcp.add_tool(FunctionTool.from_function(load_like_server(SCRIPT, name), name=name, description=name))
    return mcp


def call(mcp, tool, args):
    async def go():
        async with Client(mcp) as c:
            return await c.call_tool(tool, args, raise_on_error=False)

    res = asyncio.run(go())
    return res.structured_content


def test_schema_comes_from_signature(server):
    async def go():
        async with Client(server) as c:
            return {t.name: getattr(t, "input_schema", None) or t.inputSchema for t in await c.list_tools()}

    schemas = asyncio.run(go())
    sql = schemas["databricks_sql"]
    assert sql["required"] == ["query"]
    assert "workspace" in sql["properties"] and "params" in sql["properties"]
    assert "read-only" in sql["properties"]["query"]["description"]


def test_sql_through_mcp_keeps_text_exact(server, capfd):
    query = "SELECT region FROM s.t WHERE name LIKE '%PATH%' AND a = \"x\" AND c = '$5 & up' AND r = :r"
    out = call(server, "databricks_sql", {"query": query, "workspace": "wh", "params": {"r": "EMEA"}})
    assert out["success"] is True, out
    assert out["result"] == [{"region": "EMEA"}]
    (_, _, body), = [x for x in FakeWorkspace.requests if x[1] == "/api/2.0/sql/statements"]
    assert body["statement"] == query + "\nLIMIT 1000"
    assert body["parameters"] == [{"name": "r", "value": "EMEA"}]
    # Nothing reached the server's own stdout, which carries the MCP protocol.
    assert capfd.readouterr().out == ""


def test_sql_on_cluster_through_mcp(server):
    FakeWorkspace.cluster_state = "RUNNING"
    out = call(server, "databricks_sql", {"query": "SELECT n, ok FROM s.t WHERE n > :n", "workspace": "cl",
                                          "params": {"n:INT": "0"}, "limit": 5})
    assert out["success"] is True, out
    assert out["result"] == [{"n": "1", "ok": "true"}, {"n": "2", "ok": None}]
    assert any("2 rows" in n for n in out["notes"])


def test_blocked_sql_returns_error_not_exception(server):
    out = call(server, "databricks_sql", {"query": "DROP TABLE s.t", "workspace": "wh"})
    assert out["success"] is False
    assert "Blocked" in out["error"]
    assert FakeWorkspace.requests == []


def test_setup_and_compute_through_mcp(server):
    out = call(server, "databricks_setup", {})
    assert out["success"] is True, out
    assert "wh" in out["result"]
    out = call(server, "databricks_compute", {"workspace": "wh"})
    assert out["success"] is True and "warehouse:w1" in out["result"]


def test_registration_snippet_matches_functions():
    import inspect

    import yaml

    entries = yaml.safe_load((SCRIPT.parent.parent / "mcp_tools.databricks.yaml").read_text(encoding="utf-8"))
    assert {e["function_name"] for e in entries} == set(TOOLS) | {"databricks_login"}
    for e in entries:
        assert e["script"].endswith("/scripts/dbx_mcp.py")
        sig = inspect.signature(load_like_server(SCRIPT, e["function_name"]))
        assert set(e["parameters"]) == set(sig.parameters), e["name"]
        required = {k for k, v in e["parameters"].items() if v["required"]}
        assert required == {k for k, v in sig.parameters.items() if v.default is inspect.Parameter.empty}, e["name"]


def test_registration_paths_resolve_in_repo_layout(tmp_path):
    import shutil

    import yaml

    # <repo>/mcp/mcp_tools.yaml and <repo>/domains/fde/skills/databricks/, as the snippet expects.
    skill = SCRIPT.parent.parent
    shutil.copytree(skill / "scripts", tmp_path / "domains" / "fde" / "skills" / "databricks" / "scripts")
    (tmp_path / "mcp").mkdir()
    for e in yaml.safe_load((skill / "mcp_tools.databricks.yaml").read_text(encoding="utf-8")):
        # The server joins config_dir / script, the same as here.
        assert (tmp_path / "mcp" / e["script"]).resolve().is_file(), e["script"]
