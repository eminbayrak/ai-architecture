"""Build a scored question set from heuristic extracts. No model."""

from __future__ import annotations

from dataclasses import dataclass

WEAK_DOC = {
    "readme",
    "license",
    "licence",
    "contributing",
    "changelog",
    "index",
    "notice",
    "security",
    "code of conduct",
    "repository",
}

GENERIC_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "for",
        "with",
        "from",
        "core",
        "link",
        "home",
        "real",
        "clean",
        "help",
        "helpful",
        "image",
        "images",
        "google",
        "github",
        "claude",
        "technical",
        "spam",
        "playwright",
        "free",
        "pro",
        "vs",
        "api",
        "seo",
        "new",
        "old",
        "test",
        "tests",
        "docs",
        "doc",
        "file",
        "files",
        "page",
        "pages",
        "skill",
        "skills",
        "agent",
        "agents",
        "tool",
        "tools",
        "data",
        "search",
        "audit",
        "local",
        "global",
        "main",
        "index",
        "schema",
        "content",
        "https",
        "http",
        "www",
    }
)

PLANTED_MISS = (
    "What is the approval path for an £800 refund?",
    ("this string is never in any repo",),
    True,
    "Planted trap. This topic is not in the repo. A good skill says it found nothing instead of guessing.",
)


@dataclass(frozen=True)
class Case:
    question: str
    needles: tuple[str, ...]
    empty_ok: bool
    reason: str = ""


def _looks_like_person_name(name: str) -> bool:
    parts = name.split()
    if len(parts) >= 2:
        return all(p[0].isupper() for p in parts if p)
    if "@" in name or "-" in name:
        return True
    low = name.lower()
    if low in GENERIC_WORDS or len(name) < 4:
        return False
    return name[0].isupper() and name[1:].islower()


def _good_doc_title(name: str) -> bool:
    low = name.lower().strip()
    if low in WEAK_DOC:
        return False
    if len(name) < 8:
        return False
    if low.split()[0] in GENERIC_WORDS and len(name.split()) <= 2:
        return False
    return True


def cases_from_extract(extract: dict, limit: int = 16) -> list[Case]:
    cases: list[Case] = [
        Case(
            PLANTED_MISS[0],
            PLANTED_MISS[1],
            PLANTED_MISS[2],
            PLANTED_MISS[3],
        ),
    ]
    seen: set[str] = {PLANTED_MISS[0].lower()}

    def add(question: str, needles: list[str], reason: str, empty_ok: bool = False) -> None:
        q = question.strip()
        if q.lower() in seen or len(cases) >= limit:
            return
        clean = [n.strip() for n in needles if n and len(n.strip()) >= 3]
        if not clean and not empty_ok:
            return
        seen.add(q.lower())
        cases.append(Case(q, tuple(clean), empty_ok, reason))

    nodes = {n["name"].lower(): n for n in extract.get("nodes") or []}

    doc_candidates: list[tuple[str, str, str]] = []
    for node in extract.get("nodes") or []:
        name = str(node.get("name") or "").strip()
        desc = str(node.get("description") or "").strip()
        if node.get("type") not in {"DOCUMENT", "POLICY", "PROCESS"}:
            continue
        if not _good_doc_title(name) or len(desc) < 24:
            continue
        words = desc.split()
        if len(words) < 4:
            continue
        doc_candidates.append((name, " ".join(words[:10]), desc[:120]))
    doc_candidates.sort(key=lambda x: (-len(x[0]), x[0]))
    for name, snippet, desc in doc_candidates:
        add(
            f"What is {name}?",
            [snippet, name],
            f"The repo has a doc or section titled “{name}”. We check whether retrieval surfaces its opening text.",
        )

    for row in extract.get("aliases") or []:
        entity = str(row.get("entity") or "")
        alias = str(row.get("alias") or "")
        node = nodes.get(entity.lower())
        if not node or node.get("type") != "PERSON":
            continue
        if alias.lower() == entity.lower():
            continue
        if alias.lower() in GENERIC_WORDS or not _looks_like_person_name(entity):
            continue
        add(
            f"Who is {alias}?",
            [entity, *(node.get("description") or "").split(";")[:1]],
            f"The scan linked the name “{alias}” to “{entity}” (often a handle or short name in the docs).",
        )

    for node in extract.get("nodes") or []:
        name = str(node.get("name") or "").strip()
        desc = str(node.get("description") or "").strip()
        if node.get("type") != "PERSON":
            continue
        if not _looks_like_person_name(name):
            continue
        if "@" in name:
            add(
                f"Where is {name} mentioned?",
                [name],
                f"An email or contact ({name}) appeared in the repo scan.",
            )
            continue
        add(
            f"Who is {name}?",
            [name, *desc.split(";")[:1]],
            f"A person or handle named “{name}” was extracted from the repo (role line or owners file).",
        )

    return cases[:limit]
