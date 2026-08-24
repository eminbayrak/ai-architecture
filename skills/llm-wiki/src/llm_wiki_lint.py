"""Structural lint for the wiki. No model. No prose judgement."""

from __future__ import annotations

import re
from pathlib import Path

from llm_wiki_paths import wiki_dir

WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]+)?\]\]")
SKIP_INBOUND = {"index", "log"}


def _slug(target: str) -> str:
    return target.strip().split("/")[-1]


def lint(root: Path | None = None) -> dict:
    wiki = Path(root) if root is not None else wiki_dir()
    pages = {
        p.stem: p
        for p in wiki.rglob("*.md")
        if p.is_file()
    } if wiki.is_dir() else {}
    inbound = {k: 0 for k in pages}
    broken: list[dict] = []
    for stem, path in pages.items():
        text = path.read_text(encoding="utf-8")
        for raw in WIKILINK.findall(text):
            target = _slug(raw)
            if target in pages:
                inbound[target] += 1
            else:
                broken.append({"from": stem, "to": target})
    orphans = sorted(
        k for k, n in inbound.items() if n == 0 and k not in SKIP_INBOUND
    )
    return {
        "wiki": str(wiki),
        "pages": len(pages),
        "has_index": "index" in pages,
        "has_log": "log" in pages,
        "orphans": orphans,
        "broken": broken,
        "ok": not broken and "index" in pages,
    }
