"""Find text files in a checkout. Stdlib only. Works on Windows paths."""

from __future__ import annotations

from pathlib import Path

SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "archive",
    "meeting-notes-archive",
    "__pycache__",
    ".pytest_cache",
    ".tox",
}
SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zip", ".gz", ".exe"}
TEXT_SUFFIX = {".md", ".markdown", ".rst", ".txt", ".yaml", ".yml"}
OWNERS_NAMES = {"owners", "codeowners"}


def _skip_dir(part: str) -> bool:
    if part in SKIP_DIRS:
        return True
    if part == ".github":
        return False
    return part.startswith(".")


def is_obsidian_vault(root: Path) -> bool:
    root = Path(root)
    if (root / ".obsidian").is_dir():
        return True
    return any((root / name).is_dir() for name in ("playbooks", "engagements", "evals"))


def iter_text_files(root: Path, max_files: int = 800, max_bytes: int = 400_000) -> list[Path]:
    root = Path(root)
    found: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(_skip_dir(part) for part in path.parts[len(root.parts) : -1]):
            continue
        name = path.name.lower()
        if path.suffix.lower() in SKIP_SUFFIX:
            continue
        owners = name in OWNERS_NAMES or name.endswith("owners")
        if path.suffix.lower() not in TEXT_SUFFIX and not owners:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size < 10 or size > max_bytes:
            continue
        found.append(path)
        if len(found) >= max_files:
            break
    return found


def rel_posix(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()
