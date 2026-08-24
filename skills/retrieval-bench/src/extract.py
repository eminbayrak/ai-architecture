"""Heuristic extract: no model. Turns repo text into graph-memory-shaped JSON."""

from __future__ import annotations

import re
from pathlib import Path

from collect import rel_posix

HANDLE = re.compile(r"@([A-Za-z0-9][A-Za-z0-9-]{1,38})")
EMAIL = re.compile(r"\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
ROLE_LINE = re.compile(
    r"^[\-\*]\s+\*?\*?(?P<name>[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3})\*?\*?"
    r"\s*[—\-–:,]\s+(?P<role>.+?)\s*$",
    re.MULTILINE,
)
APPROVER = re.compile(r"^\s*-\s+([A-Za-z0-9][A-Za-z0-9-]{1,38})\s*$", re.MULTILINE)
HEADING = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)


def _owners_file(path: Path) -> bool:
    stem = path.stem.lower()
    return stem in {"owners", "codeowners"} or stem.endswith("owners")


def _title_and_lede(path: Path, text: str, rel: str) -> tuple[str, str]:
    heading = HEADING.search(text)
    title = heading.group(1).strip() if heading else path.stem.replace("-", " ").replace("_", " ")
    bits: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped in {"---", "approvers:", "reviewers:"}:
            continue
        if stripped.startswith("#") or stripped.startswith("```"):
            continue
        bits.append(stripped)
        if len(" ".join(bits)) >= 80:
            break
    lede = " ".join(bits)
    return title[:120], (lede[:240] or rel)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def extract_repo(root: Path, files: list[Path]) -> dict:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    aliases: list[dict] = []

    def add_node(name: str, typ: str, desc: str) -> None:
        key = name.lower()
        if key not in nodes:
            nodes[key] = {"name": name, "type": typ, "description": desc}
        elif desc and desc not in (nodes[key].get("description") or ""):
            nodes[key]["description"] = (
                (nodes[key].get("description") or "") + "; " + desc
            ).strip("; ")

    def add_alias(entity: str, alias: str) -> None:
        alias = alias.strip()
        if not alias or alias.lower() == entity.lower() or len(alias) < 2:
            return
        item = {"entity": entity, "alias": alias}
        if item not in aliases:
            aliases.append(item)

    def add_edge(source: str, pred: str, target: str) -> None:
        item = {"source": source, "predicate": pred, "target": target}
        if item not in edges:
            edges.append(item)

    for path in files:
        rel = rel_posix(root, path)
        text = _read(path)
        title, first = _title_and_lede(path, text, rel)
        add_node(title, "DOCUMENT", first)
        add_alias(title, path.stem)
        add_edge("repository", "references", title)

        if _owners_file(path):
            for handle in APPROVER.findall(text):
                add_node(handle, "PERSON", f"Listed in {rel}")
                add_alias(handle, handle)
                add_edge(title, "approved_by", handle)
            for handle in HANDLE.findall(text):
                add_node(handle, "PERSON", f"CODEOWNERS @{handle} in {rel}")
                add_alias(handle, handle)
                add_edge(title, "approved_by", handle)

        for match in ROLE_LINE.finditer(text):
            name = match.group("name").strip()
            role = match.group("role").strip()[:120]
            add_node(name, "PERSON", role)
            add_node(role.split(".")[0][:80], "ROLE", f"Held by {name}")
            add_edge(role.split(".")[0][:80], "held_by", name)
            add_alias(name, name.split()[0])

        for email in EMAIL.findall(text)[:8]:
            add_node(email, "PERSON", f"Contact in {rel}")
            add_alias(email, email)
            add_edge(title, "references", email)

    add_node("repository", "DOCUMENT", f"Checkout at {root.name}")
    return {
        "source_doc": "heuristic-extract",
        "nodes": list(nodes.values()),
        "edges": edges,
        "aliases": aliases,
    }
