#!/usr/bin/env python3
"""Graph memory CLI: build, recall, compare. Stdlib only. No model calls."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import runpy
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
SRC = SKILL / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from build_graph import build  # noqa: E402
from recall import recall  # noqa: E402

REFUND_QUESTION = (
    "A customer wants an £800 refund in March. Who signs it off?"
)
GRAPH_CHAIN = "Sarah Chen --[delegates_to]--> Marcus Webb"


def load_fde_kb():
    script = SKILL.parent / "fde-kb" / "scripts" / "fde_kb.py"
    if not script.is_file():
        raise SystemExit(
            "compare needs sibling skills/fde-kb (this kit's hybrid RAG path)"
        )
    spec = importlib.util.spec_from_file_location("fde_kb", script)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load {script}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fde_kb"] = mod
    spec.loader.exec_module(mod)
    return mod


def wrap_control_vault(dest: Path, corpus_before: Path | None = None) -> Path:
    """Write corpus-before as schema-valid fde-kb playbooks. Control corpus only."""
    src_dir = corpus_before or (SKILL / "corpus-before")
    playbooks = dest / "playbooks"
    playbooks.mkdir(parents=True, exist_ok=True)
    for src in sorted(src_dir.glob("*.md")):
        title = src.stem.replace("-", " ")
        body = src.read_text(encoding="utf-8")
        (playbooks / src.name).write_text(
            f"---\ntitle: {title}\ntype: playbook\n"
            f"tags: [playbook, graph-memory-control]\n---\n\n{body}\n",
            encoding="utf-8",
        )
    return dest


def compare(
    question: str,
    work: Path,
    root: Path | None = None,
) -> dict:
    root = Path(root) if root is not None else SKILL
    db_file = work / "graph.db"
    counts = build(root=root, db_file=db_file)
    prev_root = os.environ.get("GRAPH_MEMORY_ROOT")
    prev_db = os.environ.get("GRAPH_MEMORY_DB")
    os.environ["GRAPH_MEMORY_ROOT"] = str(root)
    os.environ["GRAPH_MEMORY_DB"] = str(db_file)
    try:
        facts = recall(question)
    finally:
        if prev_root is None:
            os.environ.pop("GRAPH_MEMORY_ROOT", None)
        else:
            os.environ["GRAPH_MEMORY_ROOT"] = prev_root
        if prev_db is None:
            os.environ.pop("GRAPH_MEMORY_DB", None)
        else:
            os.environ["GRAPH_MEMORY_DB"] = prev_db

    kb = load_fde_kb()
    vault = wrap_control_vault(work / "vault", root / "corpus-before")
    rag_db = work / "rag.sqlite"
    conn = kb.connect(rag_db)
    kb.init_schema(conn)
    kb.index_vault(conn, vault, None)
    hits = kb.search(conn, question, None, mode="lexical", k=8, full=True)
    conn.close()

    rag_text = "\n".join(
        f"{h.get('path', '')}\n{h.get('text', '')}" for h in hits
    )
    return {
        "question": question,
        "graph": {
            "db": counts["db"],
            "entities": counts["entities"],
            "relations": counts["relations"],
            "aliases": counts["aliases"],
            "text": facts.as_text(),
            "triples": [
                {"source": s, "predicate": p, "target": t, "source_doc": d}
                for s, p, t, d in facts.triples
            ],
            "ms": facts.ms,
        },
        "rag": {
            "mode": "lexical",
            "results": [
                {
                    "path": h.get("path"),
                    "heading": h.get("heading"),
                    "text": h.get("text"),
                }
                for h in hits
            ],
            "blob": rag_text,
        },
    }


def _cmd_build(_args: argparse.Namespace) -> int:
    counts = build()
    print(
        f"built {Path(counts['db']).name}: {counts['entities']} entities, "
        f"{counts['relations']} relations, {counts['aliases']} aliases "
        f"from {counts['docs']} docs"
    )
    for doc_name, e in counts["skipped"]:
        print(
            f"  skipped edge in {doc_name}: "
            f"{e['source']} --[{e['predicate']}]--> {e['target']} "
            f"(unknown endpoint)"
        )
    return 0


def _cmd_recall(args: argparse.Namespace) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    print(recall(args.question).as_text())
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    work = Path(args.work) if args.work else Path.cwd() / ".graph-memory-compare"
    work.mkdir(parents=True, exist_ok=True)
    report = compare(args.question, work)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _cmd_hook(_args: argparse.Namespace) -> int:
    hook = SRC / "recall_hook.py"
    runpy.run_path(str(hook), run_name="__main__")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="graph-memory")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    p_recall = sub.add_parser("recall")
    p_recall.add_argument("question")
    p_cmp = sub.add_parser("compare")
    p_cmp.add_argument("question", nargs="?", default=REFUND_QUESTION)
    p_cmp.add_argument("--work", help="scratch directory for temp graph + rag indexes")
    sub.add_parser("hook", help="UserPromptSubmit adapter (reads JSON from stdin)")
    args = parser.parse_args(argv)
    if args.cmd == "build":
        return _cmd_build(args)
    if args.cmd == "recall":
        return _cmd_recall(args)
    if args.cmd == "hook":
        return _cmd_hook(args)
    return _cmd_compare(args)


if __name__ == "__main__":
    raise SystemExit(main())
