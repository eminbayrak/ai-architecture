from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "link-skills.py"


def load_linker():
    spec = importlib.util.spec_from_file_location("link_skills", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_skill(root: Path, name: str) -> Path:
    skill = root / "skills" / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(f"---\nname: {name}\n---\n# {name}\n", encoding="utf-8")
    return skill


def test_link_skills_repairs_git_symlink_text_file(tmp_path: Path):
    """H1: Windows git checkout of mode 120000 is a text file, not a skill dir."""
    linker = load_linker()
    _fake_skill(tmp_path, "fde-kb")
    _fake_skill(tmp_path, "jira")
    dest_dir = tmp_path / ".poolside" / "skills"
    dest_dir.mkdir(parents=True)
    broken = dest_dir / "fde-kb"
    broken.write_text("../../skills/fde-kb\n", encoding="utf-8")

    results = dict(linker.link_harness(tmp_path))
    assert results["fde-kb"] == "linked"
    assert (dest_dir / "fde-kb" / "SKILL.md").is_file()
    assert (dest_dir / "jira" / "SKILL.md").is_file()


def test_link_skills_is_idempotent(tmp_path: Path):
    linker = load_linker()
    _fake_skill(tmp_path, "fde-kb")
    _fake_skill(tmp_path, "jira")
    first = dict(linker.link_harness(tmp_path))
    second = dict(linker.link_harness(tmp_path))
    assert first["fde-kb"] == "linked"
    assert second["fde-kb"] == "ok"
    assert (tmp_path / ".poolside" / "skills" / "jira" / "SKILL.md").is_file()


@pytest.mark.skipif(sys.platform == "win32", reason="unix symlink relative target")
def test_unix_link_uses_relative_target(tmp_path: Path):
    linker = load_linker()
    _fake_skill(tmp_path, "fde-kb")
    _fake_skill(tmp_path, "jira")
    linker.link_harness(tmp_path)
    dest = tmp_path / ".poolside" / "skills" / "fde-kb"
    assert dest.is_symlink()
    assert dest.readlink().as_posix() == "../../skills/fde-kb"
