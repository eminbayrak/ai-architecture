#!/usr/bin/env python3
"""Expose skills/ to Poolside without git symlinks.

Poolside only scans `.poolside/skills/`, not `skills/`. Git mode-120000
symlinks become plain text files on a normal Windows clone (no Developer
Mode, no elevation). Directory junctions need neither.

Usage (from the repo root, or anywhere):

    python scripts/link-skills.py
    py -3 scripts/link-skills.py

Idempotent. Safe to rerun. If git already left a text file named `fde-kb`
whose contents are `../../skills/fde-kb`, this deletes that file and
creates a real link.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

# Default install set. Each folder under skills/ is self-contained; pick with --skills.
HARNESS_SKILLS = ("fde-kb", "jira", "graph-memory", "llm-wiki", "retrieval-bench")

SKILL_CATALOG = (
    ("fde-kb", "Hybrid search over an Obsidian vault (pull RAG). No LLM in the indexer."),
    ("graph-memory", "Typed triples, SQLite walk (push recall). No LLM in the walker."),
    ("llm-wiki", "Compiled markdown wiki. Agent ingest; CLI query and lint."),
    ("retrieval-bench", "One-command HTML bench of retrieval skills against a repo or vault. No model API; harness agent does llm-wiki ingest."),
    ("jira", "Jira Data Center REST via a local Bearer PAT."),
)


def repo_root() -> Path:
    here = Path(__file__).resolve().parent
    if here.name == "scripts":
        return here.parent
    return Path.cwd()


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def _remove_placeholder(path: Path) -> None:
    if not path.exists() and not path.is_symlink() and not _is_link(path):
        return
    if _is_link(path) or path.is_file():
        path.unlink()
        return
    raise SystemExit(
        f"refusing to replace {path}: it is a real directory. "
        "Move it aside and rerun."
    )


def _link_unix(src: Path, dest: Path) -> None:
    rel = os.path.relpath(src, dest.parent)
    os.symlink(rel, dest, target_is_directory=True)


def _link_windows_junction(src: Path, dest: Path) -> None:
    # mklink /J needs no elevation. /D (symlink) does.
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(dest), str(src.resolve())],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "").strip()
        raise SystemExit(f"mklink /J failed for {dest}: {err}")


def link_one(src: Path, dest: Path) -> str:
    if not src.is_dir() or not (src / "SKILL.md").is_file():
        raise SystemExit(f"not a skill directory: {src}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() or _is_link(dest):
        resolved = dest.resolve() if dest.exists() or dest.is_symlink() else None
        if resolved is not None and resolved == src.resolve() and (dest / "SKILL.md").is_file():
            return "ok"
        _remove_placeholder(dest)
    if sys.platform == "win32":
        _link_windows_junction(src, dest)
    else:
        _link_unix(src, dest)
    if not (dest / "SKILL.md").is_file():
        raise SystemExit(f"link created but SKILL.md is not readable: {dest}")
    return "linked"


def available_names() -> tuple[str, ...]:
    return tuple(name for name, _desc in SKILL_CATALOG)


def parse_skill_list(raw: str | None) -> tuple[str, ...]:
    if raw is None or raw.strip() == "" or raw.strip() == "all":
        return HARNESS_SKILLS
    names = tuple(part.strip() for part in raw.split(",") if part.strip())
    known = set(available_names())
    unknown = [n for n in names if n not in known]
    if unknown:
        raise SystemExit(
            "unknown skill(s): "
            + ", ".join(unknown)
            + ". Known: "
            + ", ".join(available_names())
        )
    return names


def link_harness(root: Path, names: tuple[str, ...] | None = None) -> list[tuple[str, str]]:
    selected = names if names is not None else HARNESS_SKILLS
    results: list[tuple[str, str]] = []
    for name in selected:
        src = root / "skills" / name
        dest = root / ".poolside" / "skills" / name
        results.append((name, link_one(src, dest)))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Link selected skills/ folders into .poolside/skills/"
    )
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument(
        "--skills",
        help="comma-separated skill ids, or 'all' (default: all harness skills)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="print installable skills and exit",
    )
    args = parser.parse_args(argv)
    if args.list:
        for name, desc in SKILL_CATALOG:
            print(f"{name:16} {desc}")
        return 0
    root = (args.root or repo_root()).resolve()
    selected = parse_skill_list(args.skills)
    for name, status in link_harness(root, selected):
        print(f"{name}: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
