"""One-line repo blurb for the report. Stdlib only."""

from __future__ import annotations

import re
from pathlib import Path

from collect import is_obsidian_vault


def repo_summary(root: Path, files: list[Path]) -> dict:
    root = Path(root)
    vault = is_obsidian_vault(root)
    blurb = ""
    if vault:
        blurb = (
            f"Obsidian / fde-kb vault with {len(files)} markdown notes used for this benchmark. "
            "fde-kb indexes the vault as-is (playbooks, engagements, evals)."
        )
    readme = root / "README.md"
    if readme.is_file() and not vault:
        blurb = _readme_blurb(readme.read_text(encoding="utf-8", errors="replace"))
    elif readme.is_file() and vault and not blurb:
        blurb = _readme_blurb(readme.read_text(encoding="utf-8", errors="replace"))
    if not blurb:
        kind = "vault" if vault else "folder"
        blurb = f"A local {kind} with {len(files)} text files scanned for this benchmark."
    topics = _top_topics(files, root)
    return {
        "name": root.name,
        "blurb": blurb[:500],
        "files_scanned": len(files),
        "sample_topics": topics[:8],
        "source_kind": "obsidian_vault" if vault else "repo",
    }


def _readme_blurb(text: str) -> str:
    lines = text.splitlines()
    chunks: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if chunks:
                break
            continue
        if stripped.startswith(("#", "!", "[", "<", ">", "|", "```", "---")):
            continue
        if stripped.startswith(("[![", "![", "<img", "<p")):
            continue
        if re.match(r"^[-*]\s", stripped):
            continue
        plain = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", stripped)
        plain = re.sub(r"`+", "", plain)
        plain = re.sub(r"\*\*", "", plain)
        chunks.append(plain)
        if len(" ".join(chunks)) > 120:
            break
    return " ".join(chunks).strip()


def _top_topics(files: list[Path], root: Path) -> list[str]:
    counts: dict[str, int] = {}
    for path in files:
        rel = path.relative_to(root).as_posix()
        parts = rel.split("/")
        top = parts[0] if len(parts) > 1 else "(root)"
        if top in {".github"}:
            continue
        counts[top] = counts.get(top, 0) + 1
    ranked = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    return [f"{name} ({n} files)" for name, n in ranked[:8]]
