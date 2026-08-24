#!/usr/bin/env python3
"""llm-wiki CLI: query, lint, compile-extracts, status. Stdlib only. No model calls."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
SRC = SKILL / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from llm_wiki_compile import compile_extracts  # noqa: E402
from llm_wiki_lint import lint  # noqa: E402
from llm_wiki_paths import raw_dir  # noqa: E402
from llm_wiki_query import query  # noqa: E402


def _cmd_query(args: argparse.Namespace) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    text, _pages, _ms = query(args.question, top_k=args.top_k)
    print(text)
    return 0


def _cmd_lint(_args: argparse.Namespace) -> int:
    report = lint()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def _cmd_compile(args: argparse.Namespace) -> int:
    dest = Path(args.wiki) if args.wiki else None
    counts = compile_extracts(Path(args.extracts), dest=dest)
    print(
        f"compiled {counts['pages']} pages from {counts['docs']} extracts "
        f"({counts['entities']} entities) -> {counts['wiki']}"
    )
    return 0


def _cmd_status(_args: argparse.Namespace) -> int:
    report = lint()
    raw = raw_dir()
    raw_n = len(list(raw.glob("*.md"))) if raw.is_dir() else 0
    print(
        json.dumps(
            {
                "raw": str(raw),
                "raw_notes": raw_n,
                "wiki": report["wiki"],
                "pages": report["pages"],
                "has_index": report["has_index"],
                "orphans": len(report["orphans"]),
                "broken": len(report["broken"]),
            },
            indent=2,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="llm-wiki")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_q = sub.add_parser("query", help="lexical top pages from wiki/")
    p_q.add_argument("question")
    p_q.add_argument("--top-k", type=int, default=4)
    sub.add_parser("lint", help="broken links, orphans, missing index")
    p_c = sub.add_parser(
        "compile-extracts",
        help="project graph-memory extraction JSON into wiki pages (no model)",
    )
    p_c.add_argument("extracts", help="directory of extraction/*.json")
    p_c.add_argument("--wiki", help="output wiki directory (default: wiki/)")
    sub.add_parser("status")
    args = parser.parse_args(argv)
    if args.cmd == "query":
        return _cmd_query(args)
    if args.cmd == "lint":
        return _cmd_lint(args)
    if args.cmd == "compile-extracts":
        return _cmd_compile(args)
    return _cmd_status(args)


if __name__ == "__main__":
    raise SystemExit(main())
