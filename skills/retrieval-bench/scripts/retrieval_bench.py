#!/usr/bin/env python3
"""Retrieval bench CLI. No model API. Skills use the harness agent for ingest."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
SRC = SKILL / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from run import default_out_dir, run_bench  # noqa: E402


def _stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue


def main(argv: list[str] | None = None) -> int:
    _stdio()
    parser = argparse.ArgumentParser(
        prog="retrieval-bench",
        description=(
            "Score graph-memory, fde-kb, and llm-wiki. No model API. "
            "llm-wiki ingest is done by the harness agent (Poolside / Codex / Claude); "
            "this CLI only queries. Use --demo multihop or --repo."
        ),
    )
    parser.add_argument(
        "--demo",
        choices=("multihop",),
        help="Built-in demo. multihop = refund / delegation corpus (no --repo needed)",
    )
    parser.add_argument(
        "--repo",
        default="",
        help=(
            "GitHub URL, local repo checkout, or Obsidian / fde-kb vault folder "
            "(playbooks/, engagements/, evals/, or .obsidian/). Required unless --demo."
        ),
    )
    parser.add_argument(
        "--out",
        default="",
        help=(
            "Output directory (default: a new folder under the OS temp dir, "
            "e.g. %%TEMP%%\\retrieval-bench on Windows or $TMPDIR/retrieval-bench on macOS)"
        ),
    )
    parser.add_argument("--window", type=int, default=128000, help="Context window for token bars")
    parser.add_argument("--max-files", type=int, default=800)
    parser.add_argument("--max-questions", type=int, default=16)
    parser.add_argument("--questions", help="Optional JSON list of {question, needles, empty_ok}")
    parser.add_argument(
        "--wiki",
        help=(
            "Score an existing llm-wiki wiki/ directory the harness agent already wrote. "
            "If omitted, the bench uses compile-extracts (script pages, no model)."
        ),
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help=argparse.SUPPRESS,  # legacy no-op; bench never calls a model API
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open benchmark.html in a browser (default: open when done)",
    )
    args = parser.parse_args(argv)
    if not args.demo and not args.repo:
        parser.error("pass --demo multihop or --repo <url-or-path>")
    out = Path(args.out) if args.out else default_out_dir()
    payload = run_bench(
        repo_src=args.repo or ".",
        out=out,
        window=args.window,
        max_files=args.max_files,
        max_questions=args.max_questions,
        questions_path=Path(args.questions) if args.questions else None,
        wiki=Path(args.wiki) if args.wiki else None,
        open_html=not args.no_open,
        demo=args.demo,
    )
    html = payload["html"]
    if payload.get("demo"):
        print(f"demo {payload['demo']}")
    print(f"files {payload['files']}  entities {payload['entities']}  questions {payload['questions']}")
    raw = payload["skills"]
    ingest = payload.get("llm_wiki_ingest") or {}
    if ingest.get("mode"):
        print(f"llm-wiki: {ingest.get('mode')}")
        if ingest.get("pages") is not None:
            print(f"  pages {ingest['pages']}")
    for key, label in (
        ("graph_memory", "graph-memory"),
        ("fde_kb", "fde-kb"),
        ("llm_wiki", "llm-wiki"),
    ):
        meta = raw.get(key) or {}
        if meta.get("ran"):
            print(
                f"{label}: {meta['passed']}/{meta['total']}  "
                f"{meta['tokens_avg']} tok  {meta['ms_avg']:.1f} ms"
            )
        else:
            print(f"{label}: not scored ({meta.get('skip') or 'missing skill'})")
    print(f"out {payload.get('out_dir') or out}")
    print(html)
    print(Path(html).as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
