from __future__ import annotations

import json
from pathlib import Path

import pytest

TYPE_TO_DIR = {
    "playbook": "playbooks",
    "engagement": "engagements",
    "eval": "evals",
}


def write_note(
    vault: Path,
    note_type: str,
    slug: str,
    title: str,
    body: str,
    tags: list[str] | None = None,
) -> Path:
    folder = TYPE_TO_DIR[note_type]
    tags = tags or [note_type]
    tag_s = "[" + ", ".join(tags) + "]"
    dest = vault / folder / f"{slug}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        f"---\ntitle: {title}\ntype: {note_type}\ntags: {tag_s}\n---\n\n"
        f"# {title}\n\n{body}\n",
        encoding="utf-8",
    )
    return dest


def make_kb_vault(root: Path) -> Path:
    """Schema-valid temp vault for unit tests. Not company content."""
    vault = root / "vault"
    write_note(
        vault,
        "playbook",
        "alpha",
        "Alpha playbook",
        "## Setup\n\n"
        "ALPHAUNIQUE marker appears in this playbook section only. "
        "Staff run an evaluation. Also called evaluation harnesses (plural: harnesses).\n\n"
        "## Failure modes\n\n"
        "This failure-modes section is long enough for the chunker to keep.\n\n"
        "## Related\n\n"
        "Skip this heading content when chunking notes.\n",
        tags=["playbook"],
    )
    write_note(
        vault,
        "engagement",
        "bravo",
        "Bravo engagement",
        "BRAVOUNIQUE engagement note with enough text for indexing and tag filters.\n",
        tags=["engagement"],
    )
    write_note(
        vault,
        "eval",
        "charlie",
        "Charlie eval",
        "## Retrieval\n\n"
        "CHARLIEUNIQUE is a unique lexical marker used only in this note.\n",
        tags=["evals"],
    )
    return vault


def make_ranking_corpus(root: Path, n: int = 40) -> tuple[Path, Path]:
    """Throwaway schema-valid notes + golden JSONL. Built in tmp, never shipped."""
    vault = root / "vault"
    golden = root / "golden.jsonl"
    lines: list[str] = []
    for i in range(n):
        token = f"UNIQUEMARKER{i:03d}"
        slug = f"note-{i:03d}"
        rel = f"playbooks/{slug}.md"
        write_note(
            vault,
            "playbook",
            slug,
            f"Note {i:03d}",
            f"This note is identified by {token}. Extra text so the chunker keeps it.\n",
        )
        lines.append(json.dumps({"query": token, "path": rel}))
    golden.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return vault, golden


@pytest.fixture
def kb_vault(tmp_path: Path) -> Path:
    return make_kb_vault(tmp_path)


@pytest.fixture
def ranking_corpus(tmp_path: Path) -> tuple[Path, Path]:
    return make_ranking_corpus(tmp_path)
