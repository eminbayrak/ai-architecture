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
CLI = SKILL / "scripts" / "llm_wiki.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def lw(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_WIKI_ROOT", str(SKILL))
    monkeypatch.setenv("LLM_WIKI_WIKI", str(SKILL / "wiki"))
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    return {
        "query": _load("llm_wiki_query", SRC / "llm_wiki_query.py"),
            "lint": _load("llm_wiki_lint", SRC / "llm_wiki_lint.py"),
            "compile": _load("llm_wiki_compile", SRC / "llm_wiki_compile.py"),
        "cli": _load("llm_wiki_cli", CLI),
        "tmp": tmp_path,
    }


def test_august_credit_names_tomasz(lw):
    text, pages, _ms = lw["query"].query(
        "A customer wants a £12,000 credit in August. Who signs?"
    )
    assert "Tomasz Krol" in text
    assert "credits" in pages or "tomasz-krol" in pages or "nora-hale" in pages


def test_mileage_is_on_expenses_page(lw):
    text, pages, _ms = lw["query"].query("What is the mileage rate?")
    assert "45p" in text
    assert "expenses" in pages


def test_boss_fails_loud(lw):
    text, pages, _ms = lw["query"].query(
        "Who is in charge when the boss is away?"
    )
    assert "no wiki pages for this prompt" in text
    assert pages == []


def test_index_is_not_the_answer_dump(lw):
    text, _pages, _ms = lw["query"].query("What is the mileage rate?")
    assert "# Wiki index" not in text


def test_lint_shipped_wiki_is_clean(lw):
    report = lw["lint"].lint(SKILL / "wiki")
    assert report["has_index"]
    assert report["broken"] == []
    assert report["ok"]


def test_compile_extracts_writes_full_name_links(lw):
    ext = lw["tmp"] / "extraction"
    ext.mkdir()
    (ext / "org.json").write_text(
        json.dumps(
            {
                "source_doc": "org.md",
                "nodes": [
                    {"name": "Finance Director", "type": "ROLE", "description": "Credits"},
                    {"name": "Nora Hale", "type": "PERSON", "description": "Finance Director"},
                ],
                "edges": [
                    {"source": "Finance Director", "predicate": "held_by", "target": "Nora Hale"}
                ],
                "aliases": [{"entity": "Nora Hale", "alias": "FD"}],
            }
        ),
        encoding="utf-8",
    )
    dest = lw["tmp"] / "wiki"
    counts = lw["compile"].compile_extracts(ext, dest=dest)
    assert counts["pages"] == 2
    nora = (dest / "nora-hale.md").read_text(encoding="utf-8")
    assert "Nora Hale" in nora
    assert "FD" in nora
    fd = (dest / "finance-director.md").read_text(encoding="utf-8")
    assert "[[nora-hale|Nora Hale]]" in fd


def test_cli_query_stdout(lw):
    proc = subprocess.run(
        [sys.executable, str(CLI), "query", "What is the mileage rate?"],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "LLM_WIKI_ROOT": str(SKILL), "LLM_WIKI_WIKI": str(SKILL / "wiki")},
    )
    assert proc.returncode == 0
    assert "45p" in proc.stdout


def test_src_has_no_model_ids():
    blob = ""
    for path in SRC.glob("*.py"):
        blob += path.read_text(encoding="utf-8").lower()
    for term in ("gpt-", "claude", "openai", "anthropic"):
        assert term not in blob


def test_query_finds_facts_late_in_a_grown_page(lw):
    """A page an agent has appended to for months stays fully searchable.

    Regression: the index used to read only the first 2500 chars, so anything
    filed after that was invisible and the query returned no page at all.
    """
    wiki = lw["tmp"] / "wiki"
    wiki.mkdir()
    filler = "\n".join(
        f"## Section {i}\n\nRoutine rota detail number {i}.\n" for i in range(60)
    )
    (wiki / "operations-handbook.md").write_text(
        "# Operations handbook\n\n"
        + filler
        + "\n## Payment escalation\n\n"
        "Invoices above 50000 EUR require countersignature from the Treasury desk.\n",
        encoding="utf-8",
    )
    text, pages, _ms = lw["query"].query(
        "Who countersigns invoices at the Treasury desk?", root=wiki
    )
    assert pages == ["operations-handbook"]
    assert "Treasury desk" in text
