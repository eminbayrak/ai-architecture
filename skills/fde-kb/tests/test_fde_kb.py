from __future__ import annotations

import importlib.util
import io
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SKILL = Path(__file__).resolve().parents[1]
REPO = SKILL.parents[1]
SCRIPT = SKILL / "scripts" / "fde_kb.py"
LAUNCHER = SKILL / "scripts" / "fde-kb"
CMD_LAUNCHER = SKILL / "scripts" / "fde-kb.cmd"
PS1_LAUNCHER = SKILL / "scripts" / "fde-kb.ps1"


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


def _open(kb, db_path: Path):
    conn = kb.connect(db_path)
    kb.init_schema(conn)
    return conn


def test_chunker_splits_note_at_h2(kb):
    raw = (
        "---\ntitle: Alpha playbook\ntype: playbook\ntags: [playbook]\n---\n\n"
        "# Alpha playbook\n\n"
        "## Setup\n\nThis setup section is long enough to keep as a chunk.\n\n"
        "## Failure modes\n\nThis failure section is long enough to keep as a chunk.\n\n"
        "## Related\n\nSkip related heading content when chunking notes here.\n"
    )
    chunks = kb.chunk_markdown(raw, title="Alpha playbook")
    headings = [c.heading for c in chunks]
    assert "Setup" in headings
    assert "Failure modes" in headings
    assert "Related" not in headings
    assert all(len(c.text) >= kb.MIN_CHUNK_CHARS for c in chunks)


def test_index_and_search_returns_playbook(kb, db_path: Path, kb_vault: Path):
    conn = _open(kb, db_path)
    stats = kb.index_vault(conn, kb_vault, kb.FakeEmbedder())
    assert stats["notes"] >= 3
    assert stats["chunks"] >= 3
    result = kb.search(conn, "ALPHAUNIQUE", kb.FakeEmbedder(), mode="hybrid", k=8)
    paths = [r["path"] for r in result]
    assert "playbooks/alpha.md" in paths
    conn.close()


def test_rrf_fuses_rank_lists(kb):
    fused = kb.rrf_fuse([["a", "b", "c"], ["c", "a", "d"]], rrf_k=60)
    ids = [doc_id for doc_id, _score in fused]
    assert ids[0] in {"a", "c"}
    assert "b" in ids and "d" in ids
    scores = dict(fused)
    assert scores["a"] == pytest.approx(1 / 61 + 1 / 62)
    assert scores["c"] == pytest.approx(1 / 63 + 1 / 61)


def test_lexical_search_finds_exact_token(kb, db_path: Path, kb_vault: Path):
    conn = _open(kb, db_path)
    kb.index_vault(conn, kb_vault, kb.FakeEmbedder())
    result = kb.search(conn, "CHARLIEUNIQUE", kb.FakeEmbedder(), mode="lexical", k=5)
    assert result
    assert result[0]["path"] == "evals/charlie.md"
    conn.close()


def test_hybrid_search_includes_lexical_and_semantic_hits(kb, db_path: Path, kb_vault: Path):
    conn = _open(kb, db_path)
    kb.index_vault(conn, kb_vault, kb.FakeEmbedder())
    result = kb.search(
        conn,
        "CHARLIEUNIQUE retrieval",
        kb.FakeEmbedder(),
        mode="hybrid",
        k=8,
    )
    paths = [r["path"] for r in result]
    assert "evals/charlie.md" in paths
    conn.close()


def test_index_skips_unchanged_files(kb, db_path: Path, kb_vault: Path):
    conn = _open(kb, db_path)
    first = kb.index_vault(conn, kb_vault, kb.FakeEmbedder())
    second = kb.index_vault(conn, kb_vault, kb.FakeEmbedder())
    assert first["updated"] >= 3
    assert second["updated"] == 0
    assert second["skipped"] >= 3
    conn.close()


def test_missing_vault_exits_with_hint(kb, monkeypatch, db_path: Path):
    monkeypatch.setenv("FDE_KB_VAULT", "")
    monkeypatch.setenv("FDE_KB_VAULT_NAME", "")
    monkeypatch.setenv("FDE_KB_DB", str(db_path))
    with pytest.raises(SystemExit) as exc:
        kb.resolve_vault(env=os.environ, run_obsidian=None)
    assert exc.value.code == 1


def test_parse_vaults_verbose_tsv(kb, tmp_path: Path):
    vault = tmp_path / "KB"
    vault.mkdir()
    stdout = f"KB\t{vault}\n"
    assert kb._parse_vaults_verbose(stdout, "KB") == vault


def test_resolve_vault_from_cli_name(kb, tmp_path: Path, monkeypatch):
    vault = tmp_path / "KB"
    vault.mkdir()
    monkeypatch.setenv("FDE_KB_VAULT", "")
    monkeypatch.setenv("FDE_KB_VAULT_NAME", "KB")

    def runner(cmd, **_kwargs):
        if "verbose" in cmd:
            return SimpleNamespace(returncode=0, stdout=f"KB\t{vault}\n", stderr="")
        return SimpleNamespace(returncode=0, stdout=str(vault), stderr="")

    assert kb.resolve_vault(env=os.environ, run_obsidian=runner) == vault.resolve()


def test_status_json_reports_counts(kb, db_path: Path, monkeypatch, kb_vault: Path):
    monkeypatch.setenv("FDE_KB_VAULT", str(kb_vault))
    monkeypatch.setenv("FDE_KB_DB", str(db_path))
    conn = _open(kb, db_path)
    kb.index_vault(conn, kb_vault, kb.FakeEmbedder())
    conn.close()
    payload = kb.status_payload(
        vault=kb_vault,
        db_path=db_path,
        obsidian_ok=False,
    )
    assert payload["notes"] >= 3
    assert payload["chunks"] >= 3
    assert payload["embedding_blobs"] >= 3
    if payload["sqlite_vec"]:
        assert payload["vectors"] == payload["chunks"]
    else:
        assert payload["vectors"] == 0
    assert payload["vault"] == str(kb_vault)
    assert payload["obsidian_cli"] is False
    assert payload["embed_model"] == kb.HASH_MODEL
    assert isinstance(payload["sqlite_vec"], bool)


def test_ingest_calls_obsidian_then_indexes(kb, tmp_path: Path, db_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    calls: list[list[str]] = []

    def runner(cmd: list[str], **_kwargs):
        calls.append(cmd)
        path_arg = next(a for a in cmd if a.startswith("path="))
        rel = path_arg.split("=", 1)[1]
        content_arg = next(a for a in cmd if a.startswith("content="))
        body = content_arg.split("=", 1)[1]
        dest = vault / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body, encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    created = kb.ingest(
        vault=vault,
        db_path=db_path,
        note_type="playbook",
        title="New eval playbook",
        body="Keep identifiers out of prompts.",
        embedder=kb.FakeEmbedder(),
        run_obsidian=runner,
        vault_name="vault",
    )
    assert created.endswith("new-eval-playbook.md")
    assert calls
    assert calls[0][0] == "obsidian"
    assert calls[0][1] == "vault=vault"
    assert "create" in calls[0]
    conn = sqlite3.connect(db_path)
    n = conn.execute("select count(*) from notes").fetchone()[0]
    conn.close()
    assert n >= 1


def test_get_uses_obsidian_read(kb):
    calls: list[list[str]] = []

    def runner(cmd: list[str], **_kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="# note\n", stderr="")

    out = kb.get_note(
        rel_path="playbooks/alpha.md",
        vault_name="FDE",
        run_obsidian=runner,
    )
    assert out == "# note\n"
    assert calls[0][:3] == ["obsidian", "vault=FDE", "read"]
    assert "path=playbooks/alpha.md" in calls[0]


def test_get_falls_back_to_vault_file_when_cli_disabled(kb, tmp_path: Path):
    note = tmp_path / "playbooks" / "alpha.md"
    note.parent.mkdir(parents=True)
    note.write_text("# from disk\n", encoding="utf-8")

    def runner(cmd: list[str], **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout="Command line interface is not enabled. Please turn it on in Settings > General > Advanced.\n",
            stderr="",
        )

    out = kb.get_note(
        rel_path="playbooks/alpha.md",
        vault_name="vault",
        run_obsidian=runner,
        vault=tmp_path,
    )
    assert out == "# from disk\n"


def test_get_falls_back_to_vault_file_when_cli_unknown_vault(kb, tmp_path: Path):
    note = tmp_path / "playbooks" / "alpha.md"
    note.parent.mkdir(parents=True)
    note.write_text("# from disk\n", encoding="utf-8")

    def runner(cmd: list[str], **_kwargs):
        return SimpleNamespace(returncode=0, stdout="Vault not found.\n", stderr="")

    out = kb.get_note(
        rel_path="playbooks/alpha.md",
        vault_name="vault",
        run_obsidian=runner,
        vault=tmp_path,
    )
    assert out == "# from disk\n"


def _no_cli(_cmd, **_kwargs):
    """Obsidian unavailable, so writes take the disk fallback."""
    return SimpleNamespace(returncode=1, stdout="", stderr="no cli")


def test_normalize_tags_puts_type_first_and_dedupes(kb):
    assert kb.normalize_tags("playbook", "latency, serving,latency") == [
        "playbook",
        "latency",
        "serving",
    ]
    assert kb.normalize_tags("eval", None) == ["eval"]
    assert kb.normalize_tags("engagement", ["alpha", "engagement"]) == ["engagement", "alpha"]


def test_tags_cannot_break_the_inline_yaml_array(kb):
    rendered = kb._template_body("playbook", "T", "Body long enough to be a chunk.", ["a,b", "c[d]"])
    meta, _ = kb.parse_frontmatter(rendered)
    assert kb.note_schema_errors(meta, "playbooks/t.md") == []
    assert meta["tags"] == ["playbook", "a b", "cd"]


def test_ingest_writes_requested_tags(kb, tmp_path: Path, db_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    rel = kb.ingest(
        vault=vault,
        db_path=db_path,
        note_type="playbook",
        title="Tagged",
        body="Body text that is long enough to index cleanly.",
        embedder=kb.FakeEmbedder(),
        run_obsidian=_no_cli,
        vault_name="vault",
        tags="latency, serving",
    )
    meta, _ = kb.parse_frontmatter((vault / rel).read_text(encoding="utf-8"))
    assert meta["tags"] == ["playbook", "latency", "serving"]
    assert kb.note_schema_errors(meta, rel) == []


def test_read_body_source_prefers_file_then_argv(kb, tmp_path: Path):
    src = tmp_path / "body.md"
    src.write_text("from the file", encoding="utf-8")
    assert kb.read_body_source("from argv", str(src)) == "from the file"
    assert kb.read_body_source("from argv", "") == "from argv"


def test_read_body_source_reads_stdin(kb, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("piped body"))
    assert kb.read_body_source("", "-") == "piped body"


def test_read_body_source_missing_file_is_one_line(kb, tmp_path: Path, capsys):
    with pytest.raises(SystemExit):
        kb.read_body_source("", str(tmp_path / "nope.md"))
    err = capsys.readouterr().err.strip()
    assert "--body-file not found" in err
    assert len([ln for ln in err.splitlines() if ln.strip()]) == 1


def test_import_inherits_frontmatter_and_drops_duplicate_h1(kb, tmp_path: Path, db_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    src = tmp_path / "src.md"
    src.write_text(
        "---\ntitle: Pre-existing Title\ntags: [alpha, beta]\n---\n\n"
        "# Pre-existing Title\n\nBody text that is comfortably long enough to chunk.\n",
        encoding="utf-8",
    )
    rel = kb.import_note(
        vault=vault,
        db_path=db_path,
        source=str(src),
        note_type="engagement",
        embedder=kb.FakeEmbedder(),
        run_obsidian=_no_cli,
        vault_name="vault",
    )
    assert rel == "engagements/pre-existing-title.md"
    text = (vault / rel).read_text(encoding="utf-8")
    meta, body = kb.parse_frontmatter(text)
    assert meta["title"] == "Pre-existing Title"
    assert meta["tags"] == ["engagement", "alpha", "beta"]
    assert body.count("# Pre-existing Title") == 1
    assert kb.note_schema_errors(meta, rel) == []


def test_import_derives_title_from_h1_then_filename(kb, tmp_path: Path, db_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    common = dict(
        vault=vault,
        db_path=db_path,
        note_type="playbook",
        embedder=kb.FakeEmbedder(),
        run_obsidian=_no_cli,
        vault_name="vault",
    )
    from_h1 = tmp_path / "ignored-name.md"
    from_h1.write_text("# Title From Heading\n\nNo frontmatter, just a heading and prose.\n", encoding="utf-8")
    assert kb.import_note(source=str(from_h1), **common) == "playbooks/title-from-heading.md"

    from_name = tmp_path / "some_raw_doc.md"
    from_name.write_text("Just prose, no heading anywhere, but long enough.\n", encoding="utf-8")
    assert kb.import_note(source=str(from_name), **common) == "playbooks/some-raw-doc.md"


def test_import_missing_file_is_one_line(kb, tmp_path: Path, db_path: Path, capsys):
    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(SystemExit):
        kb.import_note(
            vault=vault,
            db_path=db_path,
            source=str(tmp_path / "nope.md"),
            note_type="playbook",
            embedder=kb.FakeEmbedder(),
            run_obsidian=_no_cli,
            vault_name="vault",
        )
    err = capsys.readouterr().err.strip()
    assert "file not found" in err
    assert "Traceback" not in err
    assert len([ln for ln in err.splitlines() if ln.strip()]) == 1


def test_cli_ingest_body_file_takes_a_document_larger_than_argv(
    kb, tmp_path: Path, db_path: Path, monkeypatch, capsys
):
    vault = tmp_path / "vault"
    vault.mkdir()
    big = tmp_path / "big.md"
    big.write_text(
        "# Big\n\n"
        + "\n\n".join(
            f"## Section {i}\n\nParagraph {i} carries enough prose to clear the minimum chunk "
            f"length several times over, so the document as a whole comfortably exceeds the "
            f"command-line limit that --body would have to squeeze through."
            for i in range(80)
        ),
        encoding="utf-8",
    )
    # cmd.exe caps a command line at 8191 characters; --body could never carry this.
    assert big.stat().st_size > 8191
    monkeypatch.setattr(kb, "default_run_obsidian", _no_cli)
    monkeypatch.setenv("FDE_KB_VAULT", str(vault))
    monkeypatch.setenv("FDE_KB_DB", str(db_path))
    monkeypatch.setenv("FDE_KB_EMBEDDER", "fake")
    code = kb.main(["ingest", "--type", "playbook", "--title", "Big doc", "--body-file", str(big)])
    assert code == 0
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["created"] is True
    written = (vault / payload["path"]).read_text(encoding="utf-8")
    assert "Section 79" in written


def test_append_calls_obsidian_and_reindexes(kb, tmp_path: Path, db_path: Path):
    vault = tmp_path / "vault"
    note = vault / "playbooks" / "x.md"
    note.parent.mkdir(parents=True)
    note.write_text("# X\n\n## Body\n\nOriginal paragraph that is long enough.\n", encoding="utf-8")
    conn = _open(kb, db_path)
    kb.index_vault(conn, vault, kb.FakeEmbedder())
    conn.close()

    def runner(cmd: list[str], **_kwargs):
        note.write_text(
            note.read_text(encoding="utf-8") + "\nappended extra context for search.\n",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    kb.append_note(
        vault=vault,
        db_path=db_path,
        rel_path="playbooks/x.md",
        body="appended extra context for search.",
        embedder=kb.FakeEmbedder(),
        run_obsidian=runner,
        vault_name="vault",
    )
    conn = sqlite3.connect(db_path)
    texts = [row[0] for row in conn.execute("select text from chunks")]
    conn.close()
    assert any("appended extra context" in t for t in texts)


def test_cli_search_json(kb, db_path: Path, monkeypatch, capsys, kb_vault: Path):
    monkeypatch.setenv("FDE_KB_VAULT", str(kb_vault))
    monkeypatch.setenv("FDE_KB_DB", str(db_path))
    monkeypatch.setenv("FDE_KB_EMBEDDER", "fake")
    code = kb.main(["index"])
    assert code == 0
    code = kb.main(["search", "ALPHAUNIQUE", "--k", "5"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["query"] == "ALPHAUNIQUE"
    assert payload["mode"] == "hybrid"
    assert any(r["path"] == "playbooks/alpha.md" for r in payload["results"])


def test_cli_missing_vault(monkeypatch, db_path: Path, capsys):
    monkeypatch.setenv("FDE_KB_VAULT", "")
    monkeypatch.setenv("FDE_KB_VAULT_NAME", "")
    monkeypatch.setenv("FDE_KB_DB", str(db_path))
    monkeypatch.setenv("FDE_KB_EMBEDDER", "fake")
    kb = load_fde_kb()
    code = kb.main(["search", "anything"])
    assert code == 1
    err = capsys.readouterr().err
    assert "FDE_KB_VAULT" in err


def test_index_skips_obsidian_and_trash(kb, tmp_path: Path, db_path: Path):
    vault = tmp_path / "vault"
    keep = vault / "playbooks" / "keep.md"
    keep.parent.mkdir(parents=True)
    keep.write_text("# Keep\n\n## Body\n\nThis visible note is long enough to index.\n", encoding="utf-8")
    hidden = vault / ".obsidian" / "hidden.md"
    hidden.parent.mkdir()
    hidden.write_text("# Hidden\n\n## Secret\n\nShould never appear in search results at all.\n", encoding="utf-8")
    trash = vault / ".trash" / "old.md"
    trash.parent.mkdir()
    trash.write_text("# Trash\n\n## Gone\n\nTrashed notes must not be indexed either.\n", encoding="utf-8")
    conn = _open(kb, db_path)
    kb.index_vault(conn, vault, kb.FakeEmbedder())
    paths = [row[0] for row in conn.execute("select path from notes")]
    conn.close()
    assert paths == ["playbooks/keep.md"]


def test_project_skill_symlink_and_launcher_exist():
    link = REPO / ".poolside" / "skills" / "fde-kb"
    assert link.is_symlink() or (link / "SKILL.md").is_file()
    assert LAUNCHER.is_file()
    assert CMD_LAUNCHER.is_file()
    assert PS1_LAUNCHER.is_file()
    skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert "name: fde-kb" in skill


def test_obsidian_missing_fails_ingest_not_index(kb, db_path: Path, monkeypatch, kb_vault: Path):
    monkeypatch.setenv("FDE_KB_VAULT", str(kb_vault))
    monkeypatch.setenv("FDE_KB_DB", str(db_path))
    monkeypatch.setenv("FDE_KB_EMBEDDER", "fake")

    def boom(*_a, **_k):
        raise FileNotFoundError("obsidian")

    code = kb.main(["index"])
    assert code == 0
    with pytest.raises(SystemExit):
        kb.get_note("playbooks/alpha.md", vault_name="v", run_obsidian=boom)


def test_get_embedder_fake_is_hash(kb, monkeypatch):
    monkeypatch.setenv("FDE_KB_EMBEDDER", "fake")
    embedder = kb.get_embedder()
    assert isinstance(embedder, kb.HashEmbedder)
    assert embedder.model_id == kb.HASH_MODEL
    assert kb.FakeEmbedder is kb.HashEmbedder


def test_get_embedder_default_is_model2vec(kb, monkeypatch, tmp_path: Path):
    monkeypatch.delenv("FDE_KB_EMBEDDER", raising=False)
    snap = tmp_path / "approved"
    snap.mkdir()
    (snap / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("FDE_KB_MODEL", str(snap))

    class Dummy:
        model_id = "minishlab/potion-base-8M"

        def encode(self, texts):
            return [[0.0] * kb.EMBED_DIM]

    monkeypatch.setattr(kb, "Model2VecEmbedder", lambda: Dummy())
    embedder = kb.get_embedder()
    assert embedder.model_id == kb.EMBED_MODEL


def test_resolve_db_windows_localappdata(kb, monkeypatch, tmp_path: Path):
    monkeypatch.setattr(kb.sys, "platform", "win32")
    monkeypatch.delenv("FDE_KB_DB", raising=False)
    monkeypatch.delenv("FDE_KB_EMBEDDER", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert kb.resolve_db() == tmp_path / "Local" / "fde-kb" / "index.sqlite"


def test_obsidian_exe_windows_localappdata(kb, monkeypatch, tmp_path: Path):
    exe = tmp_path / "Programs" / "obsidian" / "Obsidian.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    monkeypatch.setattr(kb.sys, "platform", "win32")
    monkeypatch.delenv("FDE_KB_OBSIDIAN", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(kb.shutil, "which", lambda _n: None)
    assert kb.obsidian_exe() == str(exe)


def test_launchers_use_uv_script_deps():
    bash = (SKILL / "scripts" / "fde-kb").read_text(encoding="utf-8")
    cmd = CMD_LAUNCHER.read_text(encoding="utf-8")
    ps1 = PS1_LAUNCHER.read_text(encoding="utf-8")
    py = SCRIPT.read_text(encoding="utf-8")
    assert "uv run" in bash
    assert "fde-kb.ps1" in cmd
    assert "uv run" in ps1
    assert "sqlite-vec" in py
    assert "model2vec" in py


def test_e2e_cli_index_search_get(tmp_path: Path, kb_vault: Path):
    db = tmp_path / "e2e.sqlite"
    env = os.environ.copy()
    env["FDE_KB_VAULT"] = str(kb_vault)
    env["FDE_KB_VAULT_NAME"] = "vault"
    env["FDE_KB_DB"] = str(db)
    env["FDE_KB_EMBEDDER"] = "fake"
    py = sys.executable
    index = subprocess.run(
        [py, str(SCRIPT), "index"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert index.returncode == 0, index.stderr
    stats = json.loads(index.stdout.strip().splitlines()[-1])
    assert stats["notes"] >= 3
    assert stats["chunks"] >= 3
    search = subprocess.run(
        [py, str(SCRIPT), "search", "ALPHAUNIQUE", "-k", "5"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert search.returncode == 0, search.stderr
    payload = json.loads(search.stdout.strip().splitlines()[-1])
    assert any(r["path"] == "playbooks/alpha.md" for r in payload["results"])
    got = subprocess.run(
        [py, str(SCRIPT), "get", "playbooks/alpha.md"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert got.returncode == 0, got.stderr
    assert "Alpha playbook" in got.stdout


def _can_load_sqlite_extensions() -> bool:
    conn = sqlite3.connect(":memory:")
    return hasattr(conn, "enable_load_extension")


@pytest.mark.parametrize("search_backend", ["native", "cosine"])
def test_retrieval_both_backends(kb, db_path: Path, search_backend: str, monkeypatch, kb_vault: Path):
    conn = _open(kb, db_path)
    kb.index_vault(conn, kb_vault, kb.FakeEmbedder())
    if search_backend == "cosine":
        monkeypatch.setattr(kb, "_vec_on", lambda _c: False)
    elif not kb._vec_on(conn):
        pytest.skip("this interpreter cannot load sqlite-vec; cosine backend still runs")
    result = kb.search(conn, "ALPHAUNIQUE", kb.FakeEmbedder(), mode="hybrid", k=8)
    paths = [r["path"] for r in result]
    assert "playbooks/alpha.md" in paths
    conn.close()


def test_vec0_knn_path(kb, db_path: Path, kb_vault: Path):
    required = os.environ.get("FDE_KB_REQUIRE_VEC", "").strip() == "1"
    if not _can_load_sqlite_extensions():
        if required:
            pytest.fail("FDE_KB_REQUIRE_VEC=1 but this Python cannot load SQLite extensions")
        pytest.skip("Python build cannot load SQLite extensions")
    try:
        import sqlite_vec  # noqa: F401
    except ImportError:
        if required:
            pytest.fail("FDE_KB_REQUIRE_VEC=1 but sqlite_vec is not installed")
        pytest.skip("sqlite_vec not installed")
    conn = _open(kb, db_path)
    if not kb._vec_on(conn):
        conn.close()
        if required:
            pytest.fail("FDE_KB_REQUIRE_VEC=1 but sqlite-vec did not load")
        pytest.skip("sqlite-vec did not load")
    kb.index_vault(conn, kb_vault, kb.FakeEmbedder())
    n = int(conn.execute("SELECT COUNT(*) FROM chunks_vec").fetchone()[0])
    assert n > 0
    hits = kb.search(conn, "CHARLIEUNIQUE", kb.FakeEmbedder(), mode="semantic", k=5)
    assert hits
    assert hits[0]["path"] == "evals/charlie.md"
    conn.close()


def test_eval_golden_lexical_offline(kb, db_path: Path, ranking_corpus: tuple[Path, Path]):
    vault, golden = ranking_corpus
    conn = _open(kb, db_path)
    kb.index_vault(conn, vault, kb.FakeEmbedder())
    report = kb.eval_retrieval(conn, kb.FakeEmbedder(), k=8, golden=golden)
    conn.close()
    assert report["n"] == 40
    lexical = report["modes"]["lexical"]
    hybrid = report["modes"]["hybrid"]
    assert lexical["recall_at_k"] >= 0.80
    assert lexical["mrr"] >= 0.65
    assert hybrid["mrr"] >= 0.50


def test_eval_requires_golden(kb, db_path: Path):
    conn = _open(kb, db_path)
    with pytest.raises(SystemExit) as exc:
        kb.eval_retrieval(conn, kb.FakeEmbedder(), k=8)
    conn.close()
    assert exc.value.code == 1


def test_note_schema_rejects_missing_type(kb):
    errs = kb.note_schema_errors({"title": "X", "tags": ["playbook"]}, "playbooks/x.md")
    assert any("type" in e for e in errs)


def test_golden_schema_rejects_bad_path(kb):
    errs = kb.golden_schema_errors({"query": "q", "path": "notes/x.md"})
    assert errs


def test_load_golden_rejects_invalid_line(kb, tmp_path: Path):
    bad = tmp_path / "golden.jsonl"
    bad.write_text('{"query": "q", "path": "notes/x.md"}\n', encoding="utf-8")
    with pytest.raises(SystemExit):
        kb.load_golden(bad)


def test_templates_match_note_schema(kb):
    for note_type in ("playbook", "engagement", "eval"):
        raw = kb._template_body(note_type, "Title here", "Body text here that is long enough.")
        meta, _ = kb.parse_frontmatter(raw)
        folder = kb.TYPE_TO_DIR[note_type]
        assert kb.note_schema_errors(meta, f"{folder}/title-here.md") == []


def test_index_reports_schema_invalid(kb, db_path: Path, tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "orphan.md").write_text("# Orphan\n\n## S\n\nNo frontmatter on this note at all.\n", encoding="utf-8")
    conn = _open(kb, db_path)
    stats = kb.index_vault(conn, vault, kb.FakeEmbedder())
    conn.close()
    assert stats["schema_invalid"] >= 1
    payload = kb.status_payload(vault, db_path, obsidian_ok=False)
    assert payload["schema_invalid"] >= 1
    assert any("note.schema.json" in w for w in payload["warnings"])


def test_eval_cli_uses_vault_golden(kb, db_path: Path, ranking_corpus: tuple[Path, Path], monkeypatch, capsys):
    vault, golden = ranking_corpus
    dest = vault / "evals" / "golden.jsonl"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(golden.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("FDE_KB_VAULT", str(vault))
    monkeypatch.setenv("FDE_KB_DB", str(db_path))
    monkeypatch.setenv("FDE_KB_EMBEDDER", "fake")
    code = kb.main(["eval", "-k", "8"])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["n"] == 40
    assert report["modes"]["lexical"]["recall_at_k"] >= 0.80


def test_search_tag_and_type_filters(kb, db_path: Path, kb_vault: Path):
    conn = _open(kb, db_path)
    kb.index_vault(conn, kb_vault, kb.FakeEmbedder())
    tagged = kb.search(conn, "BRAVOUNIQUE", kb.FakeEmbedder(), mode="lexical", k=8, tag="engagement")
    assert tagged
    assert all(r["path"].startswith("engagements/") for r in tagged)
    typed = kb.search(conn, "ALPHAUNIQUE", kb.FakeEmbedder(), mode="lexical", k=8, note_type="playbook")
    assert typed
    assert all(r["path"].startswith("playbooks/") for r in typed)
    conn.close()


def test_fts_quoted_phrase(kb):
    q = kb.fts_match_query('"eval harness" recovery')
    assert '"eval harness"' in q
    assert "recovery" in q


@pytest.mark.model2vec
@pytest.mark.skipif(not os.environ.get("FDE_KB_LIVE_EMBED"), reason="set FDE_KB_LIVE_EMBED=1 to run live Model2Vec eval")
def test_eval_live_model2vec(kb, db_path: Path, ranking_corpus: tuple[Path, Path], monkeypatch):
    monkeypatch.setenv("FDE_KB_ALLOW_PUBLIC_INDEX", "1")
    vault, golden = ranking_corpus
    conn = _open(kb, db_path)
    embedder = kb.Model2VecEmbedder()
    kb.index_vault(conn, vault, embedder)
    report = kb.eval_retrieval(conn, embedder, k=8, golden=golden)
    conn.close()
    lexical = report["modes"]["lexical"]
    hybrid = report["modes"]["hybrid"]
    semantic = report["modes"]["semantic"]
    assert lexical["recall_at_k"] >= 0.80
    assert hybrid["recall_at_k"] >= 0.80
    assert semantic["recall_at_k"] >= 0.80


def test_repo_does_not_ship_model_weights():
    banned = []
    for pattern in ("*.safetensors", "*.onnx", "*.bin", "*.pt", "*.pth"):
        banned.extend((SKILL / "assets").rglob(pattern))
    assert banned == [], f"model weights must not live in git: {banned}"


def test_find_model_dir_none_without_local_snapshot(kb, monkeypatch, tmp_path: Path):
    monkeypatch.delenv("FDE_KB_MODEL", raising=False)
    monkeypatch.delenv("FDE_KB_ALLOW_PUBLIC_INDEX", raising=False)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "empty-hf"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert kb.find_model_dir() is None


def test_get_embedder_without_snapshot_is_lexical(kb, monkeypatch, tmp_path: Path, capsys):
    monkeypatch.delenv("FDE_KB_EMBEDDER", raising=False)
    monkeypatch.delenv("FDE_KB_ALLOW_PUBLIC_INDEX", raising=False)
    monkeypatch.delenv("FDE_KB_MODEL", raising=False)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "empty-hf"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    warnings: list[str] = []
    embedder = kb.get_embedder(warnings)
    err = capsys.readouterr().err.strip()
    assert embedder is None
    assert kb.EMBED_REVISION in err
    assert "Hugging Face is not contacted" in err
    assert "lexical" in err.lower()
    assert "Traceback" not in err
    assert err.count("\n") == 0
    assert any("lexical" in w.lower() for w in warnings)


def test_cli_index_without_model_is_lexical_one_line(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    env = os.environ.copy()
    env["FDE_KB_EMBEDDER"] = ""
    env["FDE_KB_ALLOW_PUBLIC_INDEX"] = ""
    env["FDE_KB_VAULT"] = str(vault)
    env["FDE_KB_DB"] = str(tmp_path / "index.sqlite")
    env.pop("FDE_KB_MODEL", None)
    env["HF_HOME"] = str(tmp_path / "empty-hf")
    env["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    env["LOCALAPPDATA"] = str(tmp_path / "Local")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "index"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    err = proc.stderr.strip()
    assert "Traceback" not in err and "Traceback" not in proc.stdout
    assert "bf8b056651a2c21b8d2565580b8569da283cab23" in err
    assert "Hugging Face is not contacted" in err
    assert "lexical" in err.lower()
    lines = [ln for ln in err.splitlines() if ln.strip()]
    assert len(lines) == 1, err
    payload = json.loads(proc.stdout)
    assert payload.get("errors", 0) == 0


def test_cli_search_reports_lexical_when_model_absent(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text(
        "---\ntitle: Budget\ntype: playbook\n---\n\n# Budget\n\np95 is 400ms.\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["FDE_KB_EMBEDDER"] = ""
    env["FDE_KB_ALLOW_PUBLIC_INDEX"] = ""
    env["FDE_KB_VAULT"] = str(vault)
    env["FDE_KB_DB"] = str(tmp_path / "index.sqlite")
    env.pop("FDE_KB_MODEL", None)
    env["HF_HOME"] = str(tmp_path / "empty-hf")
    env["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    env["LOCALAPPDATA"] = str(tmp_path / "Local")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "search", "p95 budget"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["mode"] == "lexical"
    assert payload["degraded"] is True


def test_find_model_dir_uses_cache_snapshot(kb, monkeypatch, tmp_path: Path):
    monkeypatch.delenv("FDE_KB_MODEL", raising=False)
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "empty-hf"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    snap = kb.cache_model_dir()
    snap.mkdir(parents=True)
    (snap / "config.json").write_text("{}", encoding="utf-8")
    assert kb.find_model_dir() == snap.resolve()


def test_missing_model_one_line_no_traceback(kb, monkeypatch, capsys, tmp_path: Path):
    monkeypatch.delenv("FDE_KB_EMBEDDER", raising=False)
    monkeypatch.delenv("FDE_KB_ALLOW_PUBLIC_INDEX", raising=False)
    monkeypatch.setenv("FDE_KB_MODEL", str(tmp_path / "absent-model"))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "empty-hf"))
    with pytest.raises(SystemExit) as exc:
        kb.resolve_model_dir()
    assert exc.value.code == 1
    err = capsys.readouterr().err.strip()
    assert "Traceback" not in err
    assert kb.EMBED_REVISION in err
    assert "FDE_KB_MODEL" in err
    assert "Hugging Face is not contacted" in err
    assert err.count("\n") == 0


def test_resolve_model_dir_uses_local_snapshot(kb, monkeypatch, tmp_path: Path):
    snap = tmp_path / "approved"
    snap.mkdir()
    (snap / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("FDE_KB_MODEL", str(snap))
    assert kb.resolve_model_dir() == snap.resolve()


def test_resolve_model_dir_uses_hf_home_snapshot(kb, monkeypatch, tmp_path: Path):
    monkeypatch.delenv("FDE_KB_MODEL", raising=False)
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    monkeypatch.setattr(kb, "cache_model_dir", lambda: tmp_path / "no-cache-model")
    snap = (
        tmp_path
        / "hf"
        / "hub"
        / "models--minishlab--potion-base-8M"
        / "snapshots"
        / kb.EMBED_REVISION
    )
    snap.mkdir(parents=True)
    (snap / "config.json").write_text("{}", encoding="utf-8")
    assert kb.resolve_model_dir() == snap.resolve()


def test_model2vec_loads_from_resolved_local_path(kb, monkeypatch, tmp_path: Path):
    snap = tmp_path / "approved"
    snap.mkdir()
    (snap / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("FDE_KB_MODEL", str(snap))
    seen: list[str] = []

    class DummyModel:
        def encode(self, texts):
            return [[0.0] * kb.EMBED_DIM for _ in texts]

    class DummyStatic:
        @staticmethod
        def from_pretrained(path, **_kwargs):
            seen.append(str(path))
            return DummyModel()

    monkeypatch.setitem(sys.modules, "model2vec", SimpleNamespace(StaticModel=DummyStatic))
    embedder = kb.Model2VecEmbedder()
    assert seen == [str(snap.resolve())]
    assert embedder.revision == kb.EMBED_REVISION
    assert embedder.model_id == kb.EMBED_MODEL


def test_status_reports_model_without_loading(kb, db_path: Path, monkeypatch, tmp_path: Path, kb_vault: Path):
    monkeypatch.delenv("FDE_KB_EMBEDDER", raising=False)
    monkeypatch.delenv("UV_DEFAULT_INDEX", raising=False)
    monkeypatch.setenv("FDE_KB_MODEL", str(tmp_path / "absent-model"))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "empty-hf"))
    monkeypatch.setenv("FDE_KB_UV_INDEX", "https://pypi.internal.example/simple")
    payload = kb.status_payload(kb_vault, db_path, obsidian_ok=False)
    assert payload["model_revision"] == kb.EMBED_REVISION
    assert payload["model_ready"] is False
    assert payload["uv_index"] == "https://pypi.internal.example/simple"
    assert any("FDE_KB_MODEL" in w for w in payload["warnings"])


def test_launchers_require_internal_uv_index():
    bash = (SKILL / "scripts" / "fde-kb").read_text(encoding="utf-8")
    cmd = CMD_LAUNCHER.read_text(encoding="utf-8")
    ps1 = PS1_LAUNCHER.read_text(encoding="utf-8")
    for src in (bash, cmd, ps1):
        assert "UV_DEFAULT_INDEX" in src
        assert "FDE_KB_UV_INDEX" in src
        assert "FDE_KB_ALLOW_PUBLIC_INDEX" in src
        assert "Public PyPI is not used" in src
    probe = ps1.split("function Test-SqliteExt")[1].split("function Find-PythonWithExt")[0]
    assert "try" in probe and "catch" in probe


@pytest.mark.skipif(sys.platform == "win32", reason="bash launcher")
def test_bash_launcher_missing_uv_index_is_one_line(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text("#!/bin/sh\necho should-not-run\nexit 99\n", encoding="utf-8")
    uv.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    env["UV_DEFAULT_INDEX"] = ""
    env["UV_INDEX_URL"] = ""
    env["FDE_KB_UV_INDEX"] = ""
    env["FDE_KB_ALLOW_PUBLIC_INDEX"] = ""
    proc = subprocess.run(
        [str(LAUNCHER), "status"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        cwd=str(tmp_path),
    )
    assert proc.returncode == 1
    err = proc.stderr.strip()
    assert "Traceback" not in err
    assert "should-not-run" not in proc.stdout and "should-not-run" not in proc.stderr
    assert "Public PyPI is not used" in err
    assert "FDE_KB_UV_INDEX" in err
    lines = [ln for ln in err.splitlines() if ln.strip()]
    assert len(lines) == 1, err
