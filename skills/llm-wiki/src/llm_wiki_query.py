"""Lexical wiki query. Index and log are routers, not the answer dump. No model."""

from __future__ import annotations

import re
import time
from pathlib import Path

from llm_wiki_paths import wiki_dir

STOP = {
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "how",
    "what", "is", "are", "with", "from", "this", "that", "it", "as", "be",
    "who", "does", "do", "can", "we", "our", "should", "when",
}
SKIP = {"index.md", "log.md"}
WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]+)?\]\]")


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9][a-z0-9\-]*", text.lower())
    return {w for w in words if w not in STOP and len(w) > 1}


def _pages(root: Path) -> list[tuple[str, Path, str]]:
    found = []
    if not root.is_dir():
        return found
    for path in sorted(root.rglob("*.md")):
        if path.name in SKIP:
            continue
        found.append((path.stem, path, path.read_text(encoding="utf-8")))
    return found


def query(question: str, root: Path | None = None, top_k: int = 4) -> tuple[str, list[str], float]:
    t0 = time.perf_counter()
    wiki = Path(root) if root is not None else wiki_dir()
    pages = _pages(wiki)
    q = _tokens(question)
    if not q or not pages:
        ms = (time.perf_counter() - t0) * 1000
        return "(no wiki pages for this prompt)", [], ms

    ranked: list[tuple[float, str, str]] = []
    for stem, _path, text in pages:
        title = _tokens(stem.replace("-", " "))
        # Index the whole page. Capping this (it was text[:2500]) silently hid
        # every fact an agent appended past the cap, which is exactly what
        # happens to a wiki page after a few ingests.
        hay = title | _tokens(text)
        overlap = q & hay
        if not overlap:
            continue
        score = 3.0 * len(q & title) + 1.0 * len(overlap)
        ranked.append((score, stem, text))
    ranked.sort(key=lambda x: (-x[0], x[1]))
    chosen = ranked[:top_k]
    ms = (time.perf_counter() - t0) * 1000
    if not chosen:
        return "(no wiki pages for this prompt)", [], ms
    header = f"wiki: {len(chosen)} pages in {ms:.0f} ms"
    body = "\n\n".join(text for _, _, text in chosen)
    return header + "\n\n" + body, [stem for _, stem, _ in chosen], ms
