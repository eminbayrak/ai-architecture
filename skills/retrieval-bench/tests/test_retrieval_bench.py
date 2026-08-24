from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
SRC = SKILL / "src"
CLI = SKILL / "scripts" / "retrieval_bench.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def bench(monkeypatch: pytest.MonkeyPatch):
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    return {
        "collect": _load("retrieval_bench_collect", SRC / "collect.py"),
        "extract": _load("retrieval_bench_extract", SRC / "extract.py"),
        "questions": _load("retrieval_bench_questions", SRC / "questions.py"),
        "run": _load("retrieval_bench_run", SRC / "run.py"),
        "cli": _load("retrieval_bench_cli", CLI),
    }


def _mini_repo(root: Path) -> Path:
    repo = root / "acme"
    (repo / "docs").mkdir(parents=True)
    (repo / ".github").mkdir()
    (repo / "node_modules" / "pkg").mkdir(parents=True)
    (repo / "README.md").write_text(
        "# Acme Billing\n\n"
        "Acme billing is owned by Pat Lee, the finance director.\n"
        "Contact billing@acme.test for invoice questions.\n\n"
        "- Pat Lee - Finance Director\n",
        encoding="utf-8",
    )
    (repo / "OWNERS").write_text("approvers:\n- patlee\n", encoding="utf-8")
    (repo / "docs" / "refunds.md").write_text(
        "# Refunds\n\n"
        "Approvals take two working days for any credit note.\n"
        "Pat Lee signs refunds under one thousand pounds.\n",
        encoding="utf-8",
    )
    (repo / ".github" / "CODEOWNERS").write_text("* @patlee\n", encoding="utf-8")
    (repo / "node_modules" / "pkg" / "README.md").write_text(
        "# ignore me please\n\nthis file should not be collected\n",
        encoding="utf-8",
    )
    (repo / "logo.png").write_bytes(b"\x89PNG\r\n")
    return repo


def test_looks_like_git_url(bench):
    assert bench["run"].looks_like_git_url("https://github.com/org/private.git")
    assert bench["run"].looks_like_git_url("git@github.com:org/private.git")
    assert not bench["run"].looks_like_git_url("C:\\work\\private")
    assert not bench["run"].looks_like_git_url("not a repo")


def test_collect_skips_vendor_and_keeps_owners(bench, tmp_path: Path):
    repo = _mini_repo(tmp_path)
    files = bench["collect"].iter_text_files(repo)
    names = {p.name for p in files}
    assert "README.md" in names
    assert "OWNERS" in names
    assert "CODEOWNERS" in names
    assert "refunds.md" in names
    assert "logo.png" not in names
    assert not any("node_modules" in p.parts for p in files)


def test_extract_and_questions_find_needles(bench, tmp_path: Path):
    repo = _mini_repo(tmp_path)
    files = bench["collect"].iter_text_files(repo)
    extract = bench["extract"].extract_repo(repo, files)
    names = {n["name"] for n in extract["nodes"]}
    assert "patlee" in names
    assert "Pat Lee" in names
    assert "billing@acme.test" in names
    cases = bench["questions"].cases_from_extract(extract)
    assert cases[0].empty_ok
    assert cases[0].reason
    questions = [c.question for c in cases]
    assert any("Pat Lee" in q or "patlee" in q.lower() for q in questions) or any(
        "Refunds" in q for q in questions
    )
    assert not any(q == "Who is Google?" for q in questions)


def test_run_writes_html_and_scores_siblings(bench, tmp_path: Path):
    repo = _mini_repo(tmp_path)
    out = tmp_path / "out"
    payload = bench["run"].run_bench(str(repo), out, window=128000, fast=True)
    html = Path(payload["html"])
    assert html.is_file()
    text = html.read_text(encoding="utf-8")
    assert "Retrieval bench" in text
    assert "What is this repo?" in text
    assert "Run this bench again" in text
    assert "Why asked:" in text or "Planted trap" in text
    assert "128000" in text
    assert payload["files"] >= 3
    assert payload["skills"]["graph_memory"]["ran"] is True
    assert payload["skills"]["fde_kb"]["ran"] is True
    assert payload["skills"]["llm_wiki"]["ran"] is True
    assert payload.get("llm_wiki_ingest", {}).get("mode")
    planted = payload["cases"][0]
    assert planted["empty_ok"] is True
    assert planted["graph_memory"]["pass"] is True
    blobs = json.dumps(payload).lower()
    assert "patlee" in blobs or "pat lee" in blobs
    assert (out / "results.json").is_file()


def test_demo_multihop_scores_push_questions(bench, tmp_path: Path):
    out = tmp_path / "multihop-out"
    payload = bench["run"].run_bench(
        ".",
        out,
        window=128000,
        demo="multihop",
        open_html=False,
    )
    assert payload["demo"] == "multihop"
    assert payload["skills"]["graph_memory"]["ran"] is True
    assert payload["skills"]["fde_kb"]["ran"] is True
    refund = next(c for c in payload["cases"] if "£800 refund in March" in c["question"])
    assert refund["graph_memory"]["pass"] is True
    assert "Marcus Webb" in refund["graph_memory"]["text"]
    html = (out / "benchmark.html").read_text(encoding="utf-8")
    assert "Marcus" in html or "multihop" in html.lower() or "refund" in html.lower()


def test_obsidian_vault_detected(bench, tmp_path: Path):
    vault = tmp_path / "my-vault"
    (vault / "playbooks").mkdir(parents=True)
    (vault / "playbooks" / "alpha.md").write_text(
        "---\ntitle: Alpha\ntype: playbook\ntags: [playbook]\n---\n\n# Alpha\n\nALPHAUNIQUE body text here.\n",
        encoding="utf-8",
    )
    assert bench["collect"].is_obsidian_vault(vault)


def test_vault_run_uses_native_fde_kb_index(bench, tmp_path: Path):
    vault = tmp_path / "vault"
    playbooks = vault / "playbooks"
    playbooks.mkdir(parents=True)
    (playbooks / "refunds.md").write_text(
        "---\ntitle: Refunds\ntype: playbook\ntags: [playbook]\n---\n\n"
        "# Refunds\n\nPat Lee signs refunds under one thousand pounds.\n",
        encoding="utf-8",
    )
    (vault / "OWNERS").write_text("approvers:\n- patlee\n", encoding="utf-8")
    out = tmp_path / "vault-out"
    payload = bench["run"].run_bench(str(vault), out, window=128000, fast=True)
    assert payload["repo_summary"]["source_kind"] == "obsidian_vault"
    assert payload["skills"]["fde_kb"]["fde_kb_mode"] == "native Obsidian / fde-kb vault index"
    html = (out / "benchmark.html").read_text(encoding="utf-8")
    assert "Timing notes" in html or "time per question" in html.lower()
    assert "native Obsidian" in html


def test_default_out_dir_is_under_system_temp(bench):
    out = bench["run"].default_out_dir()
    assert out.is_dir()
    assert out.parent.name == "retrieval-bench"
    assert out.name.startswith("run-")


def test_cli_local_repo(bench, tmp_path: Path):
    repo = _mini_repo(tmp_path)
    out = tmp_path / "cli-out"
    code = bench["cli"].main(["--repo", str(repo), "--out", str(out), "--fast", "--no-open"])
    assert code == 0
    assert (out / "benchmark.html").is_file()


def test_needle_rank_reports_position_not_just_presence(bench):
    """A bigger dump must not score the same as a precise hit.

    `passed` is true either way; rank is what separates "the retriever found it"
    from "the retriever returned enough text that it was in there somewhere".
    """
    run = bench["run"]
    case = run.Case("who signs off refunds?", ("Marcus Webb",), False, "")
    precise = ["Sarah Chen delegates_to Marcus Webb"]
    dump = ["unrelated newsletter", "holiday rota", "team list with Marcus Webb"]

    assert run.needle_rank(precise, case) == 1
    assert run.needle_rank(dump, case) == 3
    assert run.needle_rank(["nothing relevant"], case) is None
    # Both "pass" on the blob check, which is exactly the blind spot.
    assert run.passed("\n".join(precise), case)
    assert run.passed("\n".join(dump), case)


def test_rank_stats_ignores_fail_loud_cases(bench):
    run = bench["run"]
    rows = [
        {"empty_ok": False, "rank": 1},
        {"empty_ok": False, "rank": 4},
        {"empty_ok": False, "rank": None},
        {"empty_ok": True, "rank": None},
    ]
    stats = run._rank_stats(rows)
    assert stats["ranked_cases"] == 3
    assert stats["top1"] == 1
    assert stats["top3"] == 1
    assert stats["found"] == 2
    assert stats["mrr"] == pytest.approx((1.0 + 0.25) / 3)


def test_bench_survives_an_embedder_that_exits(bench, monkeypatch, tmp_path):
    """A missing embedder costs the hybrid row, never the whole report.

    fde-kb's CLI raises SystemExit when the model snapshot is present but
    model2vec is not importable. That is right for the CLI and fatal here:
    on a locked-down machine it would kill the benchmark instead of falling
    back to lexical.
    """
    run = bench["run"]
    repo = _mini_repo(tmp_path)

    real_load = run._load

    def fake_load(name, path):
        mod = real_load(name, path)
        if name == "retrieval_bench_fde_kb":
            def boom(*_a, **_k):
                raise SystemExit(1)
            mod.get_embedder = boom
        return mod

    monkeypatch.setattr(run, "_load", fake_load)
    payload = run.run_bench(
        repo_src=str(repo),
        out=tmp_path / "out",
        max_questions=4,
        open_html=False,
    )
    kb = payload["skills"]["fde_kb"]
    assert kb["ran"] is True
    assert kb["search_mode"] == "lexical"
    assert kb["total"] > 0
