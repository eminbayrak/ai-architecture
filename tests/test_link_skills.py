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


def _fake_harness(root: Path, linker) -> None:
    for name in linker.HARNESS_SKILLS:
        _fake_skill(root, name)


def test_link_skills_repairs_git_symlink_text_file(tmp_path: Path):
    """H1: Windows git checkout of mode 120000 is a text file, not a skill dir."""
    linker = load_linker()
    _fake_harness(tmp_path, linker)
    dest_dir = tmp_path / ".poolside" / "skills"
    dest_dir.mkdir(parents=True)
    broken = dest_dir / "fde-kb"
    broken.write_text("../../skills/fde-kb\n", encoding="utf-8")

    results = dict(linker.link_harness(tmp_path))
    assert results["fde-kb"] == "linked"
    assert results["graph-memory"] in {"linked", "ok"}
    assert (dest_dir / "fde-kb" / "SKILL.md").is_file()
    assert (dest_dir / "jira" / "SKILL.md").is_file()
    assert (dest_dir / "graph-memory" / "SKILL.md").is_file()
    assert (dest_dir / "llm-wiki" / "SKILL.md").is_file()
    assert (dest_dir / "retrieval-bench" / "SKILL.md").is_file()


def test_link_skills_is_idempotent(tmp_path: Path):
    linker = load_linker()
    _fake_harness(tmp_path, linker)
    first = dict(linker.link_harness(tmp_path))
    second = dict(linker.link_harness(tmp_path))
    assert first["fde-kb"] == "linked"
    assert second["fde-kb"] == "ok"
    assert second["graph-memory"] == "ok"
    assert (tmp_path / ".poolside" / "skills" / "jira" / "SKILL.md").is_file()


@pytest.mark.skipif(sys.platform == "win32", reason="unix symlink relative target")
def test_unix_link_uses_relative_target(tmp_path: Path):
    linker = load_linker()
    _fake_harness(tmp_path, linker)
    linker.link_harness(tmp_path)
    dest = tmp_path / ".poolside" / "skills" / "fde-kb"
    assert dest.is_symlink()
    assert dest.readlink().as_posix() == "../../skills/fde-kb"
    graph = tmp_path / ".poolside" / "skills" / "graph-memory"
    assert graph.is_symlink()
    assert graph.readlink().as_posix() == "../../skills/graph-memory"


def test_link_skills_picker_installs_only_selected(tmp_path: Path):
    linker = load_linker()
    _fake_harness(tmp_path, linker)
    results = dict(linker.link_harness(tmp_path, names=("fde-kb",)))
    assert list(results) == ["fde-kb"]
    assert (tmp_path / ".poolside" / "skills" / "fde-kb" / "SKILL.md").is_file()
    assert not (tmp_path / ".poolside" / "skills" / "llm-wiki").exists()
    assert not (tmp_path / ".poolside" / "skills" / "graph-memory").exists()


def test_parse_skill_list_rejects_unknown():
    linker = load_linker()
    with pytest.raises(SystemExit):
        linker.parse_skill_list("fde-kb,not-a-skill")
    assert linker.parse_skill_list("llm-wiki") == ("llm-wiki",)
    assert linker.parse_skill_list("all") == linker.HARNESS_SKILLS
