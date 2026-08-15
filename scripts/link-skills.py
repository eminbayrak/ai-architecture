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

HARNESS_SKILLS = ("fde-kb", "jira")


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


def link_harness(root: Path) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    for name in HARNESS_SKILLS:
        src = root / "skills" / name
        dest = root / ".poolside" / "skills" / name
        results.append((name, link_one(src, dest)))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Link skills/ into .poolside/skills/")
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args(argv)
    root = (args.root or repo_root()).resolve()
    for name, status in link_harness(root):
        print(f"{name}: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
