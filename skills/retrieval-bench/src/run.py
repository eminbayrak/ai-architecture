"""Clone or open a repo, extract, auto-ask, score sibling skills, write HTML."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from collect import is_obsidian_vault, iter_text_files, rel_posix
from extract import extract_repo
from questions import Case, cases_from_extract
from repo_summary import repo_summary
from report import write_html, write_json

FAIL_LOUD = (
    "no memory matches for this prompt",
    "no wiki pages for this prompt",
    "no search hits",
)
WIN_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *{f"com{i}" for i in range(1, 10)},
    *{f"lpt{i}" for i in range(1, 10)},
}
SKILL = Path(__file__).resolve().parents[1]


def default_out_dir() -> Path:
    """Fresh run directory under the OS temp folder (Windows %TEMP%, macOS $TMPDIR)."""
    root = Path(tempfile.gettempdir()) / "retrieval-bench"
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="run-", dir=root))


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def looks_like_git_url(src: str) -> bool:
    raw = src.strip()
    if raw.startswith(("https://", "http://", "git@", "ssh://", "file://")):
        return True
    return raw.endswith(".git") and "/" in raw


def passed(text: str, case: Case) -> bool:
    blob = (text or "").lower()
    if case.empty_ok:
        if not blob.strip():
            return True
        return any(marker in blob for marker in FAIL_LOUD)
    return any(needle.lower() in blob for needle in case.needles)


def needle_rank(items: list[str], case: Case) -> int | None:
    """1-based rank of the first returned item containing a needle, else None.

    `passed` only asks whether the answer is somewhere in the blob. Because the
    skills return very different amounts of text (a chunk dump is several times
    a triple list), a bigger answer passes more often by luck alone. Rank says
    whether the retriever actually put the answer near the top.
    """
    if case.empty_ok:
        return None
    for i, item in enumerate(items, start=1):
        low = (item or "").lower()
        if any(needle.lower() in low for needle in case.needles):
            return i
    return None


def _rank_stats(rows: list[dict]) -> dict:
    """Precision-style view over the rows that actually have a ranked answer."""
    ranked = [r for r in rows if not r.get("empty_ok")]
    found = [r["rank"] for r in ranked if r.get("rank")]
    return {
        "ranked_cases": len(ranked),
        "top1": sum(1 for r in found if r == 1),
        "top3": sum(1 for r in found if r <= 3),
        "found": len(found),
        "mrr": (sum(1.0 / r for r in found) / len(ranked)) if ranked else 0.0,
    }


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def sibling(name: str) -> Path | None:
    path = SKILL.parent / name
    if (path / "SKILL.md").is_file():
        return path
    return None


@contextmanager
def env_pairs(pairs: dict[str, str]):
    prior = {key: os.environ.get(key) for key in pairs}
    os.environ.update(pairs)
    try:
        yield
    finally:
        for key, value in prior.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def resolve_repo(src: str, dest: Path) -> tuple[Path, str]:
    path = Path(src).expanduser()
    if path.exists() and path.is_dir():
        if is_obsidian_vault(path):
            return path.resolve(), "vault"
        return path.resolve(), "local"
    if not looks_like_git_url(src):
        raise SystemExit(
            f"not a local folder or git URL: {src}\n"
            "Pass a GitHub URL, a repo checkout, or an Obsidian / fde-kb vault folder."
        )
    git = shutil.which("git")
    if not git:
        raise SystemExit(
            "git is not on PATH. Install Git for Windows, or pass a folder "
            "you already cloned: --repo C:\\path\\to\\checkout"
        )
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [git, "clone", "--depth", "1", src, str(dest)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "").strip().splitlines()[-12:]
        hint = "\n".join(tail)
        raise SystemExit(
            "git clone failed. For a private repo, sign in first "
            "(gh auth login, or GitHub Git Credential Manager), "
            "or pass a folder you already cloned.\n" + hint
        )
    return dest.resolve(), "clone"


def wrap_vault(repo: Path, files: list[Path], dest: Path) -> Path:
    playbooks = dest / "playbooks"
    playbooks.mkdir(parents=True, exist_ok=True)
    used: set[str] = set()
    for path in files:
        rel = rel_posix(repo, path)
        base = re.sub(r"[^a-z0-9]+", "-", rel.lower()).strip("-")[:70] or "note"
        if base in WIN_RESERVED:
            base = f"note-{base}"
        slug = base
        n = 2
        while slug in used:
            slug = f"{base}-{n}"
            n += 1
        used.add(slug)
        body = path.read_text(encoding="utf-8", errors="replace")
        title = json.dumps(" ".join(rel.split()))
        (playbooks / f"{slug}.md").write_text(
            f"---\ntitle: {title}\ntype: playbook\n"
            f"tags: [playbook, retrieval-bench]\n---\n\n{body}\n",
            encoding="utf-8",
        )
    return dest


def _skill_stats(rows: list[dict], window: int) -> dict:
    if not rows:
        return {
            "ran": True,
            "passed": 0,
            "total": 0,
            "accuracy": 0.0,
            "tokens_avg": 0,
            "tokens_total": 0,
            "tokens_pct_window": 0.0,
            "ms_avg": 0.0,
            "ms_total": 0.0,
        }
    ok = sum(1 for row in rows if row["pass"])
    tokens = [int(row["tokens"]) for row in rows]
    ms = [float(row["ms"]) for row in rows]
    avg_tok = sum(tokens) / len(tokens)
    return {
        "ran": True,
        "passed": ok,
        "total": len(rows),
        "accuracy": ok / len(rows),
        "tokens_avg": int(round(avg_tok)),
        "tokens_total": int(sum(tokens)),
        "tokens_pct_window": (avg_tok / window) * 100 if window else 0.0,
        "ms_avg": sum(ms) / len(ms),
        "ms_total": sum(ms),
    }


def _merge_extraction_dir(ext_dir: Path) -> dict:
    """Merge graph-memory extraction/*.json into one extract-shaped dict."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    aliases: list[dict] = []
    for path in sorted(ext_dir.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        for node in doc.get("nodes") or []:
            key = str(node.get("name") or "").lower()
            if key and key not in nodes:
                nodes[key] = node
        for edge in doc.get("edges") or []:
            if edge not in edges:
                edges.append(edge)
        for alias in doc.get("aliases") or []:
            if alias not in aliases:
                aliases.append(alias)
    return {
        "source_doc": "merged-extraction",
        "nodes": list(nodes.values()),
        "edges": edges,
        "aliases": aliases,
    }


def resolve_demo(name: str) -> dict:
    """Built-in demos. multihop = refund / delegation corpus from graph-memory."""
    gm = sibling("graph-memory")
    if name == "multihop":
        if gm is None:
            raise SystemExit(
                "demo multihop needs sibling skills/graph-memory. "
                "Run: python3 scripts/link-skills.py --skills graph-memory"
            )
        before = gm / "corpus-before"
        extraction = gm / "extraction"
        questions = SKILL / "demos" / "multihop-questions.json"
        if not before.is_dir() or not extraction.is_dir() or not questions.is_file():
            raise SystemExit("demo multihop files missing under skills/graph-memory or retrieval-bench/demos")
        return {
            "repo_src": str(before),
            "questions_path": questions,
            "graph_extraction_dir": extraction,
            "label": "multihop refund / delegation demo (corpus-before + modelled extraction)",
        }
    raise SystemExit(f"unknown demo: {name}. Use: multihop")


def _run_graph(
    cases: list[Case],
    work: Path,
    extract: dict,
    window: int,
    *,
    graph_extraction_dir: Path | None = None,
) -> tuple[dict, list[dict]]:
    root = sibling("graph-memory")
    if root is None:
        return {"ran": False, "skip": "sibling graph-memory not installed"}, []
    schema = root / "src" / "schema.sql"
    if not schema.is_file():
        return {"ran": False, "skip": "graph-memory schema.sql missing"}, []
    gm_root = work / "graph-root"
    (gm_root / "src").mkdir(parents=True, exist_ok=True)
    ext_dest = gm_root / "extraction"
    if ext_dest.exists():
        shutil.rmtree(ext_dest)
    ext_dest.mkdir(parents=True, exist_ok=True)
    shutil.copy(schema, gm_root / "src" / "schema.sql")
    if graph_extraction_dir is not None:
        for path in sorted(Path(graph_extraction_dir).glob("*.json")):
            shutil.copy(path, ext_dest / path.name)
    else:
        (ext_dest / "heuristic.json").write_text(
            json.dumps(extract, indent=2) + "\n",
            encoding="utf-8",
        )
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    build_mod = _load("retrieval_bench_gm_build", src / "build_graph.py")
    recall_mod = _load("retrieval_bench_gm_recall", src / "recall.py")
    db_file = work / "graph.db"
    build_mod.build(root=gm_root, db_file=db_file)
    rows = []
    with env_pairs({"GRAPH_MEMORY_ROOT": str(gm_root), "GRAPH_MEMORY_DB": str(db_file)}):
        for case in cases:
            t0 = time.perf_counter()
            facts = recall_mod.recall(case.question)
            text = facts.as_text()
            ms = (time.perf_counter() - t0) * 1000
            items = [f"{s} {p} {t} {doc}" for s, p, t, doc in facts.triples]
            rows.append(
                {
                    "pass": passed(text, case),
                    "rank": needle_rank(items, case),
                    "returned": len(items),
                    "empty_ok": case.empty_ok,
                    "tokens": estimate_tokens(text),
                    "ms": ms,
                    "text": text,
                }
            )
    stats = _skill_stats(rows, window)
    stats.update(_rank_stats(rows))
    stats["input"] = (
        "modelled extraction JSON (hand-authored triples)"
        if graph_extraction_dir is not None
        else "heuristic regex extraction of the repo text (no model)"
    )
    stats["unit"] = "triple"
    return stats, rows


def _run_fde_kb(
    cases: list[Case],
    work: Path,
    repo: Path,
    files: list[Path],
    window: int,
    *,
    vault_mode: bool,
) -> tuple[dict, list[dict]]:
    root = sibling("fde-kb")
    script = None if root is None else root / "scripts" / "fde_kb.py"
    if script is None or not script.is_file():
        return {"ran": False, "skip": "sibling fde-kb not installed"}, []
    kb = _load("retrieval_bench_fde_kb", script)
    if vault_mode:
        vault = repo
        mode_note = "native Obsidian / fde-kb vault index"
    else:
        vault = wrap_vault(repo, files, work / "vault")
        mode_note = "repo files wrapped as temporary playbooks"
    rag_db = work / "rag.sqlite"
    conn = kb.connect(rag_db)
    kb.init_schema(conn)
    # Use the hybrid path when an approved local snapshot is present, so the
    # report is not silently scoring only half of what this skill does.
    # The fde-kb CLI exits when the snapshot is there but model2vec is not
    # importable; that is right for the CLI and wrong here, where a missing
    # embedder should cost us the hybrid row, not the whole report.
    try:
        embedder = kb.get_embedder([])
    except SystemExit:
        embedder = None
    mode = "hybrid" if embedder is not None else "lexical"
    kb.index_vault(conn, vault, embedder)
    rows = []
    for case in cases:
        t0 = time.perf_counter()
        hits = kb.search(conn, case.question, embedder, mode=mode, k=8, full=True)
        items = [f"{h.get('path', '')}\n{h.get('text', '')}" for h in hits]
        blob = "\n".join(items)
        if not hits:
            blob = "no search hits"
        ms = (time.perf_counter() - t0) * 1000
        weak = bool(hits) and max(float(h.get("coverage") or 0.0) for h in hits) < kb.WEAK_COVERAGE
        rows.append(
            {
                "pass": passed(blob, case),
                "rank": needle_rank(items, case),
                "returned": len(items),
                "empty_ok": case.empty_ok,
                "weak_match": weak,
                "tokens": estimate_tokens(blob),
                "ms": ms,
                "text": blob,
            }
        )
    conn.close()
    stats = _skill_stats(rows, window)
    stats.update(_rank_stats(rows))
    stats["fde_kb_mode"] = mode_note
    stats["search_mode"] = mode
    stats["input"] = f"raw source files, {mode} retrieval ({mode_note})"
    stats["unit"] = "chunk"
    return stats, rows


def _run_llm_wiki(
    cases: list[Case],
    work: Path,
    extract: dict,
    repo: Path,
    files: list[Path],
    window: int,
    *,
    wiki: Path | None,
) -> tuple[dict, list[dict], dict]:
    """Score llm-wiki query. Never calls a model API.

    Ingest is the harness agent's job (Poolside / Codex / Claude Code).
    This bench either:
    - scores an existing wiki/ the agent already wrote (--wiki), or
    - builds script pages via compile-extracts (no model; stand-in for bench).
    """
    root = sibling("llm-wiki")
    if root is None:
        return {"ran": False, "skip": "sibling llm-wiki not installed"}, [], {}
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    query_mod = _load("retrieval_bench_wiki_query", src / "llm_wiki_query.py")

    ingest_info: dict
    if wiki is not None:
        dest = Path(wiki)
        ingest_info = {
            "mode": "existing wiki (harness agent ingest)",
            "wiki": str(dest),
            "pages": len(list(dest.glob("*.md"))) if dest.is_dir() else 0,
        }
    else:
        compile_mod = _load("retrieval_bench_wiki_compile", src / "llm_wiki_compile.py")
        ext_dir = work / "graph-root" / "extraction"
        ext_dir.mkdir(parents=True, exist_ok=True)
        (ext_dir / "heuristic.json").write_text(
            json.dumps(extract, indent=2) + "\n",
            encoding="utf-8",
        )
        dest = work / "wiki-compile"
        compile_mod.compile_extracts(ext_dir, dest=dest)
        ingest_info = {
            "mode": (
                "compile-extracts (no model; stand-in. "
                "Real llm-wiki ingest is done by the harness agent)"
            ),
            "wiki": str(dest),
            "pages": len(list(dest.glob("*.md"))),
        }

    rows = []
    for case in cases:
        text, page_stems, ms = query_mod.query(case.question, root=dest)
        items = []
        for stem in page_stems:
            page = dest / f"{stem}.md"
            items.append(
                page.read_text(encoding="utf-8", errors="replace") if page.is_file() else stem
            )
        rows.append(
            {
                "pass": passed(text, case),
                "rank": needle_rank(items, case),
                "returned": len(items),
                "empty_ok": case.empty_ok,
                "tokens": estimate_tokens(text),
                "ms": ms,
                "text": text,
            }
        )
    stats = _skill_stats(rows, window)
    stats.update(_rank_stats(rows))
    stats["llm_wiki_ingest"] = ingest_info
    stats["input"] = str(ingest_info.get("mode") or "")
    stats["unit"] = "page"
    return stats, rows, ingest_info


def cases_from_json(path: Path) -> list[Case]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    cases = []
    for item in raw:
        cases.append(
            Case(
                str(item["question"]),
                tuple(item.get("needles") or ()),
                bool(item.get("empty_ok", False)),
                str(item.get("reason") or ""),
            )
        )
    if not cases:
        raise SystemExit(f"no questions in {path}")
    return cases


def run_bench(
    repo_src: str,
    out: Path,
    window: int = 128000,
    max_files: int = 800,
    max_questions: int = 16,
    questions_path: Path | None = None,
    wiki: Path | None = None,
    open_html: bool = False,
    graph_extraction_dir: Path | None = None,
    demo: str | None = None,
    **_legacy: object,
) -> dict:
    """Score sibling retrieval skills. Never calls a model API.

    llm-wiki ingest is the harness agent's job. Pass --wiki for an agent-built
    wiki, otherwise the bench uses compile-extracts (script pages, no model).
    Unused **_legacy accepts old fast=/ingest_model= kwargs from callers.
    """
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    demo_label = ""
    if demo:
        cfg = resolve_demo(demo)
        repo_src = str(cfg["repo_src"])
        questions_path = Path(cfg["questions_path"])
        graph_extraction_dir = Path(cfg["graph_extraction_dir"])
        demo_label = str(cfg.get("label") or demo)
    repo, origin = resolve_repo(repo_src, out / "checkout")
    vault_mode = origin == "vault" or is_obsidian_vault(repo)
    files = iter_text_files(repo, max_files=max_files)
    if graph_extraction_dir is not None:
        extract = _merge_extraction_dir(Path(graph_extraction_dir))
    else:
        extract = extract_repo(repo, files)
    if questions_path is not None:
        cases = cases_from_json(Path(questions_path))
        questions_origin = "hand-written"
    else:
        cases = cases_from_extract(extract, limit=max_questions)
        # These questions are derived from `extract`, which is also what
        # graph-memory and llm-wiki are built from. Those two are being asked
        # about their own input; fde-kb reads the original files. Record it so
        # the report can say so instead of presenting the scores as neutral.
        questions_origin = "auto-generated from the same extract graph-memory and llm-wiki are built from"
    (out / "extract.json").write_text(json.dumps(extract, indent=2) + "\n", encoding="utf-8")
    write_json(
        out / "questions.json",
        [
            {
                "question": c.question,
                "needles": list(c.needles),
                "empty_ok": c.empty_ok,
                "reason": c.reason,
            }
            for c in cases
        ],
    )

    skills: dict[str, dict] = {}
    per_case = [
        {
            "question": c.question,
            "empty_ok": c.empty_ok,
            "needles": list(c.needles),
            "reason": c.reason,
        }
        for c in cases
    ]

    gm_meta, gm_rows = _run_graph(
        cases,
        out,
        extract,
        window,
        graph_extraction_dir=Path(graph_extraction_dir) if graph_extraction_dir else None,
    )
    skills["graph_memory"] = gm_meta
    for i, row in enumerate(gm_rows):
        per_case[i]["graph_memory"] = row

    kb_meta, kb_rows = _run_fde_kb(
        cases, out, repo, files, window, vault_mode=vault_mode
    )
    skills["fde_kb"] = kb_meta
    for i, row in enumerate(kb_rows):
        per_case[i]["fde_kb"] = row

    wiki_meta, wiki_rows, ingest_info = _run_llm_wiki(
        cases,
        out,
        extract,
        repo,
        files,
        window,
        wiki=Path(wiki) if wiki else None,
    )
    skills["llm_wiki"] = wiki_meta
    for i, row in enumerate(wiki_rows):
        per_case[i]["llm_wiki"] = row

    summary = repo_summary(repo, files)
    if demo_label:
        summary = dict(summary)
        summary["blurb"] = (
            f"{demo_label}. "
            f"{summary.get('blurb') or ''}"
        ).strip()
    payload = {
        "repo": repo_src,
        "repo_path": str(repo),
        "origin": origin,
        "demo": demo or "",
        "out_dir": str(out.resolve()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": window,
        "files": len(files),
        "entities": len(extract.get("nodes") or []),
        "relations": len(extract.get("edges") or []),
        "aliases": len(extract.get("aliases") or []),
        "questions": len(cases),
        "questions_origin": questions_origin,
        "repo_summary": summary,
        "llm_wiki_ingest": ingest_info,
        "fast": True,
        "skills": skills,
        "cases": per_case,
    }
    html_path = out / "benchmark.html"
    write_json(out / "results.json", payload)
    write_html(html_path, payload)
    if open_html:
        _open_file(html_path)
    payload["html"] = str(html_path.resolve())
    return payload


def _open_file(path: Path) -> None:
    target = str(path.resolve())
    try:
        if sys.platform == "win32":
            os.startfile(target)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", target], check=False)
        else:
            subprocess.run(["xdg-open", target], check=False)
    except OSError as exc:
        print(f"could not open browser: {exc}", file=sys.stderr)
