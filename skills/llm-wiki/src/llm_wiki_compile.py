"""Project extraction JSON (graph-memory shape) into wiki pages. No model call."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from llm_wiki_paths import wiki_dir


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "page"


def merge_extracts(ext_dir: Path) -> dict:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    aliases: list[dict] = []
    sources: dict[str, set[str]] = defaultdict(set)
    files = sorted(Path(ext_dir).glob("*.json"))
    for path in files:
        doc = json.loads(path.read_text(encoding="utf-8"))
        src = doc.get("source_doc") or path.name
        for n in doc.get("nodes") or []:
            key = n["name"].lower()
            if key not in nodes:
                nodes[key] = dict(n)
            else:
                old = nodes[key].get("description") or ""
                new = n.get("description") or ""
                if new and new not in old:
                    nodes[key]["description"] = (old + "; " + new).strip("; ")
            sources[key].add(src)
        for e in doc.get("edges") or []:
            if e not in edges:
                edges.append(e)
        for a in doc.get("aliases") or []:
            if a not in aliases:
                aliases.append(a)
    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "aliases": aliases,
        "sources": {k: sorted(v) for k, v in sources.items()},
        "docs": len(files),
    }


def compile_extracts(ext_dir: Path, dest: Path | None = None) -> dict:
    dest = Path(dest) if dest is not None else wiki_dir()
    dest.mkdir(parents=True, exist_ok=True)
    bundle = merge_extracts(ext_dir)

    aliases_by: dict[str, list[str]] = defaultdict(list)
    for a in bundle["aliases"]:
        aliases_by[a["entity"].lower()].append(a["alias"])

    inbound: dict[str, list[tuple[str, str]]] = defaultdict(list)
    outbound: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for e in bundle["edges"]:
        outbound[e["source"].lower()].append((e["predicate"], e["target"]))
        inbound[e["target"].lower()].append((e["predicate"], e["source"]))

    by_type: dict[str, list[str]] = defaultdict(list)
    written = 0
    for n in bundle["nodes"]:
        name = n["name"]
        key = name.lower()
        slug = slugify(name)
        typ = n.get("type") or "DOCUMENT"
        desc = n.get("description") or ""
        als = aliases_by.get(key, [])
        srcs = bundle["sources"].get(key, [])
        lines = [f"# {name}", "", f"Type: {typ}", ""]
        if desc:
            lines += [desc, ""]
        if als:
            lines += ["## Aliases", ""]
            lines += [f"- {a}" for a in als]
            lines += [""]
        if outbound[key] or inbound[key]:
            lines += ["## Links", ""]
            for pred, target in outbound[key]:
                lines.append(f"- [[{slugify(target)}|{target}]] ({pred})")
            for pred, source in inbound[key]:
                lines.append(f"- [[{slugify(source)}|{source}]] ({pred}, inbound)")
            lines += [""]
        if srcs:
            lines += ["## Sources", ""]
            lines += [f"- {s}" for s in srcs]
            lines += [""]
        (dest / f"{slug}.md").write_text("\n".join(lines), encoding="utf-8")
        written += 1
        by_type[typ].append(name)

    index_lines = [
        "# Wiki index",
        "",
        "Catalog only. Open a page for facts. Write full names on pages, not only slugs.",
        "",
    ]
    for typ in sorted(by_type):
        index_lines += [f"## {typ}", ""]
        for name in sorted(by_type[typ]):
            index_lines.append(f"- [[{slugify(name)}|{name}]]")
        index_lines.append("")
    (dest / "index.md").write_text("\n".join(index_lines), encoding="utf-8")
    return {
        "wiki": str(dest),
        "pages": written,
        "docs": bundle["docs"],
        "entities": len(bundle["nodes"]),
        "relations": len(bundle["edges"]),
    }
