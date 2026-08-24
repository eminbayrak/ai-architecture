"""Regression tests for known failure modes in fde-kb.

Each test encodes one audit finding. They must pass without xfail.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "fde_kb.py"
CMD_LAUNCHER = SKILL / "scripts" / "fde-kb.cmd"
PS1_LAUNCHER = SKILL / "scripts" / "fde-kb.ps1"
BASH_LAUNCHER = SKILL / "scripts" / "fde-kb"


def load_fde_kb():
    spec = importlib.util.spec_from_file_location("fde_kb", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fde_kb"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def kb():
    return load_fde_kb()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "index.sqlite"


def cli_missing(_cmd, **_kwargs):
    """Obsidian CLI absent, so the disk fallback path runs."""
    return SimpleNamespace(returncode=1, stdout="", stderr="Vault not found.")


def _open(kb, db_path: Path):
    conn = kb.connect(db_path)
    kb.init_schema(conn)
    return conn


# --------------------------------------------------------------------- B3


def test_b3_failed_embedder_does_not_destroy_the_index(kb, db_path: Path, kb_vault: Path):
    conn = _open(kb, db_path)
    kb.index_vault(conn, kb_vault, kb.HashEmbedder())
    before = conn.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL").fetchone()[0]
    assert before > 0

    kb.index_vault(conn, kb_vault, None)
    after = conn.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL").fetchone()[0]
    conn.close()
    assert after == before, "a model load failure must not destroy stored vectors"


def test_b3_model_change_requires_force(kb, db_path: Path, kb_vault: Path):
    conn = _open(kb, db_path)
    kb.index_vault(conn, kb_vault, kb.HashEmbedder())

    class Other:
        model_id = "other-model"
        revision = "local"
        dim = kb.EMBED_DIM

        def encode(self, texts):
            return [[0.0] * kb.EMBED_DIM for _ in texts]

    with pytest.raises(SystemExit) as exc:
        kb.index_vault(conn, kb_vault, Other())
    assert exc.value.code == 1
    after = conn.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL").fetchone()[0]
    assert after > 0
    kb.index_vault(conn, kb_vault, Other(), force=True)
    model = conn.execute("SELECT value FROM meta WHERE key = 'embed_model'").fetchone()[0]
    conn.close()
    assert model == "other-model"


def test_b3_db_path_is_namespaced_per_embedder(kb, monkeypatch):
    monkeypatch.delenv("FDE_KB_DB", raising=False)
    monkeypatch.setenv("FDE_KB_EMBEDDER", "fake")
    fake_db = kb.resolve_db()
    monkeypatch.delenv("FDE_KB_EMBEDDER", raising=False)
    real_db = kb.resolve_db()

    assert fake_db != real_db, "FDE_KB_EMBEDDER=fake must not be able to reach a real index"


# --------------------------------------------------------------------- B4


def test_b4_get_rejects_relative_traversal(kb, tmp_path: Path):
    vault = tmp_path / "vault"
    (vault / "playbooks").mkdir(parents=True)
    (tmp_path / "outside-secret.txt").write_text("SECRET OUTSIDE THE VAULT\n", encoding="utf-8")

    with pytest.raises(SystemExit):
        kb.get_note(
            "../outside-secret.txt",
            vault_name="v",
            run_obsidian=cli_missing,
            vault=vault,
        )


def test_b4_append_rejects_relative_traversal(kb, tmp_path: Path, db_path: Path):
    vault = tmp_path / "nest" / "vault"
    (vault / "playbooks").mkdir(parents=True)
    escaped = (vault / ".." / ".." / "escaped.md").resolve()

    with pytest.raises(SystemExit):
        kb.append_note(
            vault=vault,
            db_path=db_path,
            rel_path="../../escaped.md",
            body="written outside the vault",
            embedder=kb.HashEmbedder(),
            run_obsidian=cli_missing,
            vault_name="v",
        )
    assert not escaped.exists(), "the write must be rejected before it happens"


def test_b4_absolute_and_unc_paths_do_not_escape(kb, tmp_path: Path):
    vault = tmp_path / "vault"
    (vault / "playbooks").mkdir(parents=True)
    with pytest.raises(SystemExit):
        kb.resolve_in_vault(vault, "C:/Users/emin/.ssh/id_rsa")
    with pytest.raises(SystemExit):
        kb.resolve_in_vault(vault, r"\\evil-share\x\note.md")
    with pytest.raises(SystemExit):
        kb.resolve_in_vault(vault, "/etc/passwd")

    outside = tmp_path / "outside.txt"
    outside.write_text("SECRET\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        kb.get_note(str(outside), vault_name="v", run_obsidian=cli_missing, vault=vault)


# --------------------------------------------------------------------- B5


def test_b5_windows_launcher_propagates_exit_codes():
    cmd = CMD_LAUNCHER.read_text(encoding="utf-8")
    stale = "exit /b %ERRORLEVEL%" in cmd and "EnableDelayedExpansion" not in cmd
    assert not stale, (
        "cmd.exe expands %ERRORLEVEL% when it parses the whole if(...) block, before the "
        "python run executes, so the launcher always returns a stale code. Use "
        "setlocal EnableDelayedExpansion with !ERRORLEVEL!, or a single exit outside the blocks."
    )
    assert "EnableDelayedExpansion" in cmd
    assert "exit /b !ERRORLEVEL!" in cmd


@pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe launcher")
def test_b5_windows_cmd_nonzero_on_missing_vault(tmp_path: Path):
    env = os.environ.copy()
    env["FDE_KB_VAULT"] = ""
    env["FDE_KB_VAULT_NAME"] = ""
    env["FDE_KB_DB"] = str(tmp_path / "missing.sqlite")
    env["FDE_KB_EMBEDDER"] = "fake"
    env["FDE_KB_ALLOW_PUBLIC_INDEX"] = "1"
    proc = __import__("subprocess").run(
        ["cmd", "/c", str(CMD_LAUNCHER), "search", "anything"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0


# --------------------------------------------------------------------- D1, D2


def test_d1_append_does_not_silently_drop_duplicate_text(kb, tmp_path: Path, db_path: Path):
    vault = tmp_path / "vault"
    note = vault / "playbooks" / "log.md"
    note.parent.mkdir(parents=True)
    line = "- follow up with the customer about scope"
    note.write_text(f"# Log\n\n## Entries\n\n{line}\n", encoding="utf-8")
    before = note.read_text(encoding="utf-8")

    kb.append_note(
        vault=vault,
        db_path=db_path,
        rel_path="playbooks/log.md",
        body=line,
        embedder=kb.HashEmbedder(),
        run_obsidian=cli_missing,
        vault_name="vault",
    )

    after = note.read_text(encoding="utf-8")
    assert after != before, "appending an existing line must append it, not silently discard it"
    assert after.count(line) == 2


def test_d2_ingest_does_not_silently_discard_a_colliding_body(kb, tmp_path: Path, db_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    common = dict(
        vault=vault,
        db_path=db_path,
        note_type="playbook",
        embedder=kb.HashEmbedder(),
        run_obsidian=cli_missing,
        vault_name="vault",
    )
    first = kb.ingest(title="Eval harness", body="FIRST BODY", **common)
    second = kb.ingest(title="Eval harness", body="SECOND BODY that the user wanted saved", **common)

    written = "".join(p.read_text(encoding="utf-8") for p in vault.rglob("*.md"))
    assert "SECOND BODY" in written, (
        f"second ingest returned {second!r} (first was {first!r}) but wrote nothing. "
        "Suffix, append, or fail, but never report success having written nothing."
    )
    assert first != second


# --------------------------------------------------------------------- D3, D4


def test_d3_long_paragraphs_are_split_not_truncated(kb):
    tail = "UNIQUEENDMARKER"
    body = "startmarker " + ("filler words here " * 200) + tail
    chunks = kb.chunk_markdown(f"# T\n\n## S\n\n{body}\n")

    assert any(tail in c.text for c in chunks), (
        "text past MAX_CHUNK_CHARS is dropped from the index entirely and is unfindable"
    )


def test_d4_chunker_tracks_code_fences(kb):
    raw = (
        "---\ntags: [playbook]\n---\n\n"
        "```bash\n"
        "# Install the eval harness dependencies before you start the run\n"
        "uv sync --group dev\n"
        "```\n\n"
        "# Real Playbook Title\n\n"
        "## Setup\n\n"
        "Some real setup prose that is definitely long enough to survive the filter.\n"
    )
    assert kb.note_title(raw, Path("x.md")) == "Real Playbook Title", (
        "a shell comment inside a fenced block is being used as the note title"
    )


# --------------------------------------------------------------------- D5, D6


def test_d5_obsidian_result_is_inspected_not_discarded():
    src = SCRIPT.read_text(encoding="utf-8")
    discarded = re.findall(r"^\s*_obsidian_error\([^)]*\)\s*$", src, flags=re.MULTILINE)
    assert not discarded, (
        f"{len(discarded)} call site(s) invoke _obsidian_error and throw the result away, so a "
        "failed CLI write is indistinguishable from a successful one"
    )


def test_d6_status_vectors_reflects_the_vector_index(kb, db_path: Path, kb_vault: Path):
    conn = _open(kb, db_path)
    kb.index_vault(conn, kb_vault, kb.HashEmbedder())
    vec_on = kb._vec_on(conn)
    try:
        indexed = conn.execute("SELECT COUNT(*) FROM chunks_vec").fetchone()[0]
    except sqlite3.Error:
        indexed = 0
    conn.close()

    payload = kb.status_payload(kb_vault, db_path, obsidian_ok=False)
    assert payload["sqlite_vec"] == vec_on
    assert payload["vectors"] == indexed, (
        "status reports stored embedding blobs, so it claims a healthy vector index even when "
        "chunks_vec is empty or absent. This is the instrument you would use to diagnose B3."
    )


# --------------------------------------------------------------------- W1, W2, W4, W5


def test_w1_obsidian_subprocess_decodes_as_utf8():
    src = SCRIPT.read_text(encoding="utf-8")
    body = src.split("def default_run_obsidian")[1].split("\nCLI_DISABLED_MSG")[0]
    assert "encoding=" in body, (
        "subprocess.run(text=True) with no encoding= falls back to cp1252 on Windows, so "
        "non-ASCII note content from Obsidian mojibakes or raises UnicodeDecodeError"
    )


def test_w2_stdout_is_utf8_safe():
    src = SCRIPT.read_text(encoding="utf-8")
    assert "reconfigure" in src or "PYTHONIOENCODING" in src or "stdout.buffer" in src, (
        "`get` does sys.stdout.write(raw_note_text) with no UTF-8 reconfigure, which is a "
        "UnicodeEncodeError on a legacy Windows console"
    )


def test_w4_slugify_is_windows_safe(kb):
    long_title = (
        "How we run retrieval evaluations for customers with strict data residency and PHI "
        "constraints in regulated healthcare engagements during discovery"
    )
    assert len(kb.slugify(long_title)) <= 80, "unbounded slugs push past MAX_PATH on Windows"

    reserved = {"con", "prn", "aux", "nul", "com1", "lpt1"}
    for name in ["AUX", "con", "NUL", "com1", "LPT1"]:
        assert kb.slugify(name) not in reserved, (
            f"slugify({name!r}) produces a reserved Windows device name, unwritable with any extension"
        )


def test_w5_windows_launcher_probes_for_sqlite_extensions():
    cmd = CMD_LAUNCHER.read_text(encoding="utf-8")
    ps1 = PS1_LAUNCHER.read_text(encoding="utf-8")
    bash = BASH_LAUNCHER.read_text(encoding="utf-8")
    assert "enable_load_extension" in bash
    assert "fde-kb.ps1" in cmd
    assert "enable_load_extension" in ps1, (
        "Windows PowerShell launcher must probe for a Python that can load vec0"
    )


# --------------------------------------------------------------------- R1, R2


def test_r1_query_analyser_is_unicode_aware(kb):
    assert kb.fts_match_query("evaluation") == "evaluation"
    assert "değerlendirme" in kb.fts_match_query("değerlendirme")
    assert kb.fts_match_query("café") != "caf"
    assert kb.fts_match_query("評価") != '""'


def test_r2_lexical_search_stems(kb, db_path: Path, kb_vault: Path):
    conn = _open(kb, db_path)
    kb.index_vault(conn, kb_vault, kb.HashEmbedder())
    hits = {
        q: len(kb.search(conn, q, kb.HashEmbedder(), mode="lexical", k=5))
        for q in ("eval", "evaluation", "harness", "harnesses")
    }
    conn.close()

    assert hits["evaluation"] > 0 and hits["harnesses"] > 0, (
        f"against a corpus that contains evaluation/harnesses: {hits}. "
        "Changing the tokenizer requires an FTS rebuild, so pair this with O4."
    )


# --------------------------------------------------------------------- O1, O2, O3, O4, O5


def test_o1_search_reports_index_freshness(kb, db_path: Path, monkeypatch, capsys, kb_vault: Path):
    monkeypatch.setenv("FDE_KB_VAULT", str(kb_vault))
    monkeypatch.setenv("FDE_KB_DB", str(db_path))
    monkeypatch.setenv("FDE_KB_EMBEDDER", "fake")
    assert kb.main(["index"]) == 0
    assert kb.main(["search", "ALPHAUNIQUE"]) == 0

    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "indexed_at" in payload or "stale" in payload, (
        "the agent cannot distinguish an empty KB from a stale index, and after the first "
        "index new notes never appear until someone reruns `index`"
    )


def test_o2_status_does_not_load_the_embedding_model(kb, db_path: Path, monkeypatch, capsys, kb_vault: Path):
    monkeypatch.setenv("FDE_KB_VAULT", str(kb_vault))
    monkeypatch.setenv("FDE_KB_DB", str(db_path))
    monkeypatch.delenv("FDE_KB_EMBEDDER", raising=False)

    built: list[str] = []

    class Spy:
        model_id = kb.EMBED_MODEL

        def __init__(self, *_a, **_k):
            built.append("loaded")

        def encode(self, texts):
            return [[0.0] * kb.EMBED_DIM for _ in texts]

    monkeypatch.setattr(kb, "Model2VecEmbedder", Spy)
    kb.main(["status"])
    capsys.readouterr()

    assert built == [], (
        "counting rows in SQLite pays a full model load, which on a cold cache fetches 10 files "
        "from the HF Hub and prints a progress bar to stderr"
    )


def test_o3_index_reports_which_files_failed(kb, tmp_path: Path, db_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "ok.md").write_text("# Ok\n\n## S\n\nA perfectly fine note with enough text.\n", encoding="utf-8")
    (vault / "bad.md").write_bytes(b"# Bad\n\n## S\n\n\xff\xfe not valid utf8 \xff\n")

    conn = _open(kb, db_path)
    stats = kb.index_vault(conn, vault, kb.HashEmbedder())
    conn.close()

    assert stats["errors"] == 1
    failed = stats.get("error_paths") or stats.get("errors_detail")
    assert failed and "bad.md" in str(failed), (
        f"stats were {stats}. An anonymous error count is not actionable by the agent or by you."
    )


def test_o4_schema_is_versioned(kb, db_path: Path, kb_vault: Path):
    conn = _open(kb, db_path)
    kb.index_vault(conn, kb_vault, kb.HashEmbedder())
    meta = {row[0]: row[1] for row in conn.execute("SELECT key, value FROM meta")}
    conn.close()

    assert "schema_version" in meta, (
        f"meta holds {sorted(meta)}. With CREATE TABLE IF NOT EXISTS everywhere and no version, "
        "any future column silently fails to apply to an existing DB, and a model with a "
        "different dim fails into the swallowed except sqlite3.Error: pass."
    )
    assert meta["schema_version"] == kb.SCHEMA_VERSION
    assert meta["embed_revision"] == "local"
    assert meta["chunker_version"] == kb.CHUNKER_VERSION


def test_o5_vec_flag_is_not_keyed_by_object_id():
    src = SCRIPT.read_text(encoding="utf-8")
    assert "_VEC_ENABLED[id(" not in src and "_VEC_ENABLED.get(id(" not in src, (
        "40 open/close cycles produced 2 distinct id() values, and the dict is never cleaned. "
        "Use a sqlite3.Connection subclass passed as factory= instead."
    )
    assert "factory=KbConnection" in src


# ===================================================================
# Second review pass. Same ratchet: xfail(strict=True) today, delete the
# marker as part of the fix.
# ===================================================================


def test_b2b_golden_set_detects_a_ranking_regression(
    kb, db_path: Path, ranking_corpus: tuple[Path, Path]
):
    """Corpus larger than k so an unranked retriever drops recall@8."""
    vault, golden = ranking_corpus
    conn = _open(kb, db_path)
    kb.index_vault(conn, vault, kb.HashEmbedder())
    cases = kb.load_golden(golden)
    baseline = kb.eval_retrieval(conn, kb.HashEmbedder(), k=8, cases=cases)["modes"]["lexical"]

    original = kb._lexical_ids

    def unranked(c, _query, limit):
        # Worst realistic regression: BM25 stops ranking and returns rowid order.
        return [int(r[0]) for r in c.execute("SELECT id FROM chunks ORDER BY id LIMIT ?", (limit,))]

    kb._lexical_ids = unranked
    try:
        broken = kb.eval_retrieval(conn, kb.HashEmbedder(), k=8, cases=cases)["modes"]["lexical"]
    finally:
        kb._lexical_ids = original
    conn.close()

    assert broken["recall_at_k"] < baseline["recall_at_k"] - 0.05, (
        f"a totally unranked retriever still scores recall@8={broken['recall_at_k']:.3f} "
        f"against baseline {baseline['recall_at_k']:.3f}, so the CI gate "
        "(recall_at_k >= 0.8, mrr > 0) passes for a broken ranker. Either grow the eval "
        "corpus well past k, drop k for the gate, or gate on MRR, which does move "
        f"(baseline {baseline['mrr']:.3f} -> broken {broken['mrr']:.3f})."
    )


def test_b1b_vec0_coverage_is_required_on_at_least_one_ci_job():
    """test_vec0_knn_path is guarded by skipif + importorskip + a runtime skip.
    If all three CI platforms happen to lack loadable extensions, CI stays green
    with the headline retrieval path never executed, exactly as before the fix."""
    workflow = (SKILL.parents[1] / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
    suite = (SKILL / "tests" / "test_fde_kb.py").read_text(encoding="utf-8")

    assert "FDE_KB_REQUIRE_VEC" in workflow, (
        "no CI job asserts that the vec0 path actually ran. Set FDE_KB_REQUIRE_VEC=1 on the "
        "job whose interpreter can load extensions."
    )
    assert "FDE_KB_REQUIRE_VEC" in suite, (
        "the vec0 test does not honour a required-vec gate, so it can still skip silently"
    )


def test_search_payload_reports_vector_degradation(kb, db_path: Path, monkeypatch, capsys, kb_vault: Path):
    """conn.vec_degraded is set when a vec0 query fails and the code falls back to
    cosine, and status surfaces it. The search payload does not, so the agent that
    ran the search is the one caller who cannot tell.

    Reproduced on a vec0-capable interpreter by recreating chunks_vec at float[128]:
    stderr said 'chunks_vec query failed; cosine fallback: Dimension mismatch',
    exit code 0, payload.degraded False, payload.warnings []."""
    monkeypatch.setenv("FDE_KB_VAULT", str(kb_vault))
    monkeypatch.setenv("FDE_KB_DB", str(db_path))
    monkeypatch.setenv("FDE_KB_EMBEDDER", "fake")
    assert kb.main(["index"]) == 0
    capsys.readouterr()

    original = kb._semantic_ids

    def degrading(conn, query, embedder, limit):
        conn.vec_degraded = True  # what the real vec0 failure path does
        return original(conn, query, embedder, limit)

    monkeypatch.setattr(kb, "_semantic_ids", degrading)
    assert kb.main(["search", "ALPHAUNIQUE"]) == 0
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    assert payload["degraded"] is True, (
        f"payload was degraded={payload['degraded']} warnings={payload['warnings']}. "
        "A silent drop from vec0 KNN to in-process cosine changes result quality and the "
        "caller is never told."
    )


def test_obsidian_probe_uses_a_short_timeout(kb, monkeypatch):
    """obsidian_cli_ok() runs on every `status`. On macOS, when Obsidian is installed
    but not already running, the GUI binary never answers, so the probe burns the whole
    FDE_KB_OBSIDIAN_TIMEOUT. Measured: obsidian_cli_ok() -> False in exactly the timeout,
    and it is what makes `status` a 20 second command. This is W3's macOS twin: a health
    check should give up in a second or two, while a real write can still wait 20."""
    monkeypatch.delenv("FDE_KB_OBSIDIAN_TIMEOUT", raising=False)
    monkeypatch.setattr(kb, "obsidian_exe", lambda: "/Applications/Obsidian.app/Contents/MacOS/obsidian")
    seen: list[float] = []

    def record(*_a, **kwargs):
        seen.append(float(kwargs.get("timeout", 0)))
        raise kb.subprocess.TimeoutExpired(cmd="obsidian", timeout=kwargs.get("timeout", 0))

    monkeypatch.setattr(kb.subprocess, "run", record)
    kb.obsidian_cli_ok()

    assert seen and seen[0] <= 5, (
        f"the health probe waits {seen[0] if seen else '?'}s. Give obsidian_cli_ok() its own "
        "short timeout so `status` and `get` cannot stall for 20 seconds when Obsidian is "
        "installed but not running."
    )


# ===================================================================
# Third review pass.
# ===================================================================


def _vec_capable() -> bool:
    return hasattr(sqlite3.connect(":memory:"), "enable_load_extension")


def _require_vec_or_skip(kb, conn) -> None:
    """Same escalation the suite already uses for test_vec0_knn_path."""
    required = os.environ.get("FDE_KB_REQUIRE_VEC", "").strip() == "1"
    if not _vec_capable():
        conn.close()
        if required:
            pytest.fail("FDE_KB_REQUIRE_VEC=1 but this Python cannot load SQLite extensions")
        pytest.skip("this Python cannot load SQLite extensions")
    if not kb._vec_on(conn):
        conn.close()
        if required:
            pytest.fail("FDE_KB_REQUIRE_VEC=1 but sqlite-vec did not load")
        pytest.skip("sqlite-vec did not load")


def test_n3_empty_vector_table_is_detected(kb, db_path: Path, kb_vault: Path):
    """Reproduced across two real interpreters, no sabotage:

      index with .venv 3.13.7 (no vec0) -> 8 embedding blobs, chunks_vec absent
      search with Homebrew 3.12 (vec0)  -> init_schema creates chunks_vec EMPTY

    _semantic_ids then queries an empty vec table, gets 0 rows and no sqlite3.Error,
    so the cosine fallback never fires:

      search --mode semantic : 0 results  degraded=False  warnings=[]  stderr silent
      search --mode hybrid   : silently lexical-only
      status : sqlite_vec=True vectors=0 embedding_blobs=8 degraded=False

    The whole semantic half is gone and every instrument reports healthy. Both
    interpreters resolve to the same default DB path, so 'index once under pytest,
    then use the launcher' is enough to produce it.

    status already has both numbers. It just never compares them. The stronger fix
    is to backfill chunks_vec from chunks.embedding, which is a pure derived rebuild
    from vectors that are already stored.
    """
    conn = _open(kb, db_path)
    _require_vec_or_skip(kb, conn)

    kb.index_vault(conn, kb_vault, kb.HashEmbedder())
    blobs = conn.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL").fetchone()[0]
    assert blobs > 0
    conn.execute("DELETE FROM chunks_vec")  # what a no-vec0 indexer leaves behind
    conn.commit()
    conn.close()

    payload = kb.status_payload(kb_vault, db_path, obsidian_ok=True)
    assert payload["sqlite_vec"] is True
    assert payload["vectors"] == 0 and payload["embedding_blobs"] == blobs

    assert payload["degraded"] is True, (
        f"status reports vectors={payload['vectors']} embedding_blobs={payload['embedding_blobs']} "
        f"sqlite_vec=True degraded={payload['degraded']}. An unpopulated vector index means "
        "semantic search silently returns nothing and hybrid quietly becomes lexical-only. "
        "Flag it when vectors < embedding_blobs, or backfill chunks_vec from the stored blobs."
    )


# ===================================================================
# Install / launcher regression coverage.
# ===================================================================


def test_harness_skill_links_resolve_on_this_platform():
    """Git mode-120000 symlinks become plain text files on a normal Windows clone.

    After clone, `python scripts/link-skills.py` creates directory junctions
    (Windows, no elevation) or relative symlinks (unix). This test fails when
    `.poolside/skills/<name>` is missing or is a text file containing the
    git-symlink payload.
    """
    repo = SKILL.parents[1]
    linker_spec = importlib.util.spec_from_file_location(
        "link_skills_audit",
        repo / "scripts" / "link-skills.py",
    )
    assert linker_spec is not None and linker_spec.loader is not None
    linker = importlib.util.module_from_spec(linker_spec)
    linker_spec.loader.exec_module(linker)
    broken = []
    for name in linker.HARNESS_SKILLS:
        entry = repo / ".poolside" / "skills" / name
        if (entry / "SKILL.md").is_file():
            continue
        if entry.is_file():
            payload = entry.read_text(encoding="utf-8", errors="replace").strip()[:60]
            broken.append(f"{name}: text file containing {payload!r}")
        elif not entry.exists():
            broken.append(f"{name}: missing (run python scripts/link-skills.py)")
        else:
            broken.append(f"{name}: does not resolve to a skill directory")

    assert not broken, (
        "harness skill links do not resolve on this platform: "
        + "; ".join(broken)
        + ". Run: python scripts/link-skills.py"
    )
