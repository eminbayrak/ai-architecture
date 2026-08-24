from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
SRC = SKILL / "src"
CLI = SKILL / "scripts" / "graph_memory.py"
HOOK = SRC / "recall_hook.py"
RUNTIME_FILES = (SRC / "build_graph.py", SRC / "recall.py", SRC / "paths.py")
FORBIDDEN_MODEL_TERMS = ("fable", "haiku", "claude", "gpt-")

REFUND = "A customer wants an £800 refund in March. Who signs it off?"
SUPPLIER = "Who approves supplier payments over £2,000?"
ONBOARDING = "What is the onboarding process?"
PRIYA = "What does Priya do?"
BOSS = "Who is in charge when the boss is away?"
CHAIN = "Sarah Chen --[delegates_to]--> Marcus Webb"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def gm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GRAPH_MEMORY_ROOT", str(SKILL))
    monkeypatch.setenv("GRAPH_MEMORY_DB", str(tmp_path / "graph.db"))
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    paths = _load("paths", SRC / "paths.py")
    build_mod = _load("build_graph", SRC / "build_graph.py")
    recall_mod = _load("recall", SRC / "recall.py")
    cli = _load("graph_memory_cli", CLI)
    counts = build_mod.build()
    return {
        "build": build_mod,
        "recall": recall_mod,
        "cli": cli,
        "paths": paths,
        "counts": counts,
        "db": tmp_path / "graph.db",
    }


def test_entity_id_is_content_addressed(gm):
    first = gm["build"].entity_id("ROLE", "Ops Manager")
    second = gm["build"].entity_id("ROLE", "ops manager")
    assert first == second
    assert first == gm["build"].entity_id("ROLE", "Ops Manager")


def test_build_counts_and_is_deterministic(gm, tmp_path: Path):
    assert gm["counts"]["docs"] == 8
    assert gm["counts"]["entities"] > 0
    assert gm["counts"]["relations"] > 0
    assert gm["counts"]["aliases"] > 0
    assert gm["counts"]["skipped"] == []
    other = tmp_path / "graph2.db"
    again = gm["build"].build(root=SKILL, db_file=other)
    assert again["entities"] == gm["counts"]["entities"]
    assert again["relations"] == gm["counts"]["relations"]
    assert again["aliases"] == gm["counts"]["aliases"]


def test_refund_recalls_marcus_webb(gm):
    facts = gm["recall"].recall(REFUND)
    text = facts.as_text()
    assert CHAIN in text
    assert "Marcus Webb" in text
    assert "Ops Manager" in text
    assert "Sarah Chen" in text
    assert "£500" in text or "500" in text
    assert len(facts.triples) <= 8
    assert facts.triples


def test_supplier_payments_alex_doyle(gm):
    text = gm["recall"].recall(SUPPLIER).as_text()
    assert "Alex Doyle" in text
    assert "Founder" in text


def test_onboarding_ops_manager_and_checklist(gm):
    text = gm["recall"].recall(ONBOARDING).as_text()
    assert "Ops Manager" in text
    assert "day one" in text.lower() or "day-one" in text.lower() or "checklist" in text.lower()


def test_priya_alias_to_support_lead(gm):
    text = gm["recall"].recall(PRIYA).as_text()
    assert "Support Lead" in text
    assert "Priya Patel" in text


def test_boss_away_fails_loudly(gm):
    facts = gm["recall"].recall(BOSS)
    text = facts.as_text()
    assert facts.triples == []
    assert "no memory matches for this prompt" in text


def test_hook_json_shape(gm, tmp_path: Path):
    env = os.environ.copy()
    env["GRAPH_MEMORY_ROOT"] = str(SKILL)
    env["GRAPH_MEMORY_DB"] = str(gm["db"])
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"prompt": REFUND}),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    extra = payload["hookSpecificOutput"]
    assert extra["hookEventName"] == "UserPromptSubmit"
    assert CHAIN in extra["additionalContext"]
    assert payload["systemMessage"].startswith("memory:")


def test_runtime_has_no_model_ids():
    blob = "\n".join(p.read_text(encoding="utf-8").lower() for p in RUNTIME_FILES)
    for term in FORBIDDEN_MODEL_TERMS:
        assert term not in blob, term


def test_compare_pull_finds_rule_push_walks_hops(gm, tmp_path: Path):
    report = gm["cli"].compare(REFUND, tmp_path / "work", root=SKILL)
    graph_text = report["graph"]["text"]
    rag_blob = report["rag"]["blob"]
    assert CHAIN in graph_text
    assert "Marcus Webb" in graph_text
    assert CHAIN not in rag_blob
    assert "£500" in rag_blob or "500" in rag_blob
    paths = [r["path"] for r in report["rag"]["results"]]
    assert any("customer-ops-handbook" in (p or "") for p in paths) or any(
        "refund" in (p or "") for p in paths
    )
