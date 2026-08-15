from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
REPO = SKILL.parents[1]
JIRA = SKILL / "scripts" / "jira"
PS1 = SKILL / "scripts" / "jira.ps1"
PY = SKILL / "scripts" / "jira.py"


def _pwsh():
    return shutil.which("pwsh") or shutil.which("powershell")


def _run(args: list[str], env: dict[str, str] | None = None, stdin: str | None = None):
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not installed")
    merged = os.environ.copy()
    merged.pop("JIRA_TOKEN", None)
    merged.pop("JIRA_BASE", None)
    merged.pop("JIRA_BASE_URL", None)
    merged.pop("JIRA_ENV_FILE", None)
    merged.pop("JIRA_TOKEN_FILE", None)
    merged.pop("USERPROFILE", None)
    if env:
        merged.update(env)
    return subprocess.run(
        [bash, str(JIRA), *args],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(REPO),
        env=merged,
        input=stdin,
    )


def _run_ps(args: list[str], env: dict[str, str] | None = None, stdin: str | None = None):
    exe = _pwsh()
    if not exe:
        pytest.skip("PowerShell not installed")
    merged = os.environ.copy()
    merged.pop("JIRA_TOKEN", None)
    merged.pop("JIRA_BASE", None)
    merged.pop("JIRA_BASE_URL", None)
    merged.pop("JIRA_ENV_FILE", None)
    merged.pop("JIRA_TOKEN_FILE", None)
    merged.pop("USERPROFILE", None)
    if env:
        merged.update(env)
    return subprocess.run(
        [exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(PS1), *args],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(REPO),
        env=merged,
        input=stdin,
    )


def _run_py(args: list[str], env: dict[str, str] | None = None, stdin: str | None = None):
    merged = os.environ.copy()
    merged.pop("JIRA_TOKEN", None)
    merged.pop("JIRA_BASE", None)
    merged.pop("JIRA_BASE_URL", None)
    merged.pop("JIRA_ENV_FILE", None)
    merged.pop("JIRA_TOKEN_FILE", None)
    merged.pop("USERPROFILE", None)
    if env:
        merged.update(env)
    return subprocess.run(
        [sys.executable, str(PY), *args],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(REPO),
        env=merged,
        input=stdin,
    )


def test_parse_key_from_bare_key():
    result = _run(["parse-key", "PROJ-123"])
    assert result.returncode == 0
    assert result.stdout.strip() == "PROJ-123"


def test_parse_key_from_browse_url():
    result = _run(["parse-key", "https://jira.example.com/browse/ACME-99?focusedCommentId=1"])
    assert result.returncode == 0
    assert result.stdout.strip() == "ACME-99"


def test_parse_key_from_rest_url():
    result = _run(["parse-key", "https://jira.example.com/rest/api/2/issue/OPS-7"])
    assert result.returncode == 0
    assert result.stdout.strip() == "OPS-7"


def test_dry_run_get_joins_base_and_uses_bearer():
    result = _run(
        ["--dry-run", "GET", "/rest/api/2/issue/PROJ-123"],
        env={
            "JIRA_BASE": "https://jira.example.com/",
            "JIRA_TOKEN": "super-secret-token",
        },
    )
    assert result.returncode == 0
    out = result.stdout + result.stderr
    assert "https://jira.example.com/rest/api/2/issue/PROJ-123" in out
    assert "Bearer ***" in out or "Authorization: Bearer ***" in out
    assert "super-secret-token" not in out
    assert "curl" in out


def test_missing_token_fails_fast(tmp_path: Path):
    result = _run(
        ["--dry-run", "GET", "/rest/api/2/myself"],
        env={
            "JIRA_BASE": "https://jira.example.com",
            "HOME": str(tmp_path),
        },
    )
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "token" in combined.lower()
    assert "super-secret" not in combined


def test_missing_base_fails_fast(tmp_path: Path):
    result = _run(
        ["--dry-run", "GET", "/rest/api/2/myself"],
        env={"JIRA_TOKEN": "tok", "HOME": str(tmp_path)},
    )
    assert result.returncode != 0
    assert "JIRA_BASE" in (result.stdout + result.stderr)


def test_token_file_is_used(tmp_path: Path):
    token_file = tmp_path / "jira.token"
    token_file.write_text("file-secret-token\n", encoding="utf-8")
    result = _run(
        ["--dry-run", "GET", "/rest/api/2/myself"],
        env={
            "JIRA_BASE": "https://jira.example.com",
            "JIRA_TOKEN_FILE": str(token_file),
        },
    )
    assert result.returncode == 0
    out = result.stdout + result.stderr
    assert "file-secret-token" not in out
    assert "https://jira.example.com/rest/api/2/myself" in out


def test_token_file_from_userprofile(tmp_path: Path):
    profile = tmp_path / "Users" / "me"
    token = profile / ".config" / "atlassian" / "jira.token"
    token.parent.mkdir(parents=True)
    token.write_text("profile-secret-token\n", encoding="utf-8")
    result = _run(
        ["--dry-run", "GET", "/rest/api/2/myself"],
        env={
            "JIRA_BASE": "https://jira.example.com",
            "HOME": str(tmp_path / "empty-home"),
            "USERPROFILE": str(profile),
        },
    )
    assert result.returncode == 0
    out = result.stdout + result.stderr
    assert "profile-secret-token" not in out
    assert "https://jira.example.com/rest/api/2/myself" in out


def test_find_transition_id_from_fixture():
    payload = {
        "transitions": [
            {"id": "11", "name": "To Do", "to": {"name": "To Do"}},
            {"id": "21", "name": "Start Progress", "to": {"name": "In Progress"}},
        ]
    }
    result = _run(["find-transition", "In Progress"], stdin=json.dumps(payload))
    assert result.returncode == 0
    assert result.stdout.strip() == "21"


def test_dry_run_comment_posts_json_and_hides_token():
    result = _run(
        ["--dry-run", "comment", "PROJ-1", "shipped"],
        env={
            "JIRA_BASE": "https://jira.example.com",
            "JIRA_TOKEN": "super-secret-token",
        },
    )
    assert result.returncode == 0
    out = result.stdout + result.stderr
    assert "POST" in out
    assert "/rest/api/2/issue/PROJ-1/comment" in out
    assert "super-secret-token" not in out
    assert "shipped" in out


def test_dry_run_create_issue():
    result = _run(
        ["--dry-run", "create", "PROJ", "Need eval harness"],
        env={
            "JIRA_BASE": "https://jira.example.com",
            "JIRA_TOKEN": "tok",
        },
    )
    assert result.returncode == 0
    out = result.stdout + result.stderr
    assert "POST" in out
    assert "/rest/api/2/issue" in out
    assert "Need eval harness" in out
    assert "PROJ" in out


def test_ps1_parse_key_from_browse_url():
    result = _run_ps(
        ["parse-key", "https://jira.example.com/browse/ACME-99?focusedCommentId=1"]
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "ACME-99"


def test_ps1_dry_run_hides_token():
    result = _run_ps(
        ["--dry-run", "GET", "/rest/api/2/issue/PROJ-123"],
        env={
            "JIRA_BASE": "https://jira.example.com/",
            "JIRA_TOKEN": "super-secret-token",
        },
    )
    assert result.returncode == 0
    out = result.stdout + result.stderr
    assert "https://jira.example.com/rest/api/2/issue/PROJ-123" in out
    assert "Bearer ***" in out
    assert "super-secret-token" not in out
    assert "curl" in out


def test_ps1_token_file_from_userprofile(tmp_path: Path):
    profile = tmp_path / "Users" / "me"
    token = profile / ".config" / "atlassian" / "jira.token"
    token.parent.mkdir(parents=True)
    token.write_text("profile-secret-token\n", encoding="utf-8")
    result = _run_ps(
        ["--dry-run", "GET", "/rest/api/2/myself"],
        env={
            "JIRA_BASE": "https://jira.example.com",
            "HOME": str(tmp_path / "empty-home"),
            "USERPROFILE": str(profile),
        },
    )
    assert result.returncode == 0
    out = result.stdout + result.stderr
    assert "profile-secret-token" not in out
    assert "https://jira.example.com/rest/api/2/myself" in out


def test_ps1_raw_method_does_not_also_run_the_get_shortcut():
    """A PowerShell switch matches case-insensitively and runs every matching branch.

    Without -CaseSensitive and break, `GET` fell through into `^get$` as well, so the
    request was emitted twice and any path without an issue key exited 1.
    """
    result = _run_ps(
        ["--dry-run", "GET", "/rest/api/2/issue/PROJ-123"],
        env={"JIRA_BASE": "https://jira.example.com", "JIRA_TOKEN": "tok"},
    )
    assert result.returncode == 0
    assert result.stdout.count("curl -sS -X GET") == 1, result.stdout


def test_ps1_get_shortcut_still_resolves_an_issue_key():
    result = _run_ps(
        ["--dry-run", "get", "https://jira.example.com/browse/ACME-99"],
        env={"JIRA_BASE": "https://jira.example.com", "JIRA_TOKEN": "tok"},
    )
    assert result.returncode == 0
    assert "/rest/api/2/issue/ACME-99" in result.stdout


def test_py_parse_key_from_browse_url():
    result = _run_py(
        ["parse-key", "https://jira.example.com/browse/ACME-99?focusedCommentId=1"]
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "ACME-99"


def test_py_dry_run_hides_token():
    result = _run_py(
        ["--dry-run", "GET", "/rest/api/2/issue/PROJ-123"],
        env={
            "JIRA_BASE": "https://jira.example.com/",
            "JIRA_TOKEN": "super-secret-token",
        },
    )
    assert result.returncode == 0
    out = result.stdout + result.stderr
    assert "https://jira.example.com/rest/api/2/issue/PROJ-123" in out
    assert "Bearer ***" in out
    assert "super-secret-token" not in out
    assert "curl" in out


def test_py_find_transition_id_from_fixture():
    payload = {
        "transitions": [
            {"id": "11", "name": "To Do", "to": {"name": "To Do"}},
            {"id": "21", "name": "Start Progress", "to": {"name": "In Progress"}},
        ]
    }
    result = _run_py(["find-transition", "In Progress"], stdin=json.dumps(payload))
    assert result.returncode == 0
    assert result.stdout.strip() == "21"


def test_py_reads_env_from_parent_directory(tmp_path: Path):
    root = tmp_path / "proj"
    nested = root / "deep"
    nested.mkdir(parents=True)
    (root / ".env").write_text(
        "export JIRA_BASE=https://jira.example.com\nJIRA_TOKEN=from-parent-env\n",
        encoding="utf-8",
    )
    merged = os.environ.copy()
    for key in (
        "JIRA_TOKEN",
        "JIRA_BASE",
        "JIRA_BASE_URL",
        "JIRA_ENV_FILE",
        "JIRA_TOKEN_FILE",
        "USERPROFILE",
    ):
        merged.pop(key, None)
    merged["HOME"] = str(tmp_path / "empty-home")
    result = subprocess.run(
        [sys.executable, str(PY), "--dry-run", "GET", "/rest/api/2/myself"],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(nested),
        env=merged,
    )
    assert result.returncode == 0
    out = result.stdout + result.stderr
    assert "https://jira.example.com/rest/api/2/myself" in out
    assert "from-parent-env" not in out
