#!/usr/bin/env python3
"""Last-resort Jira CLI: stdlib only (urllib). Prefer bash or PowerShell wrappers.

Do not print JIRA_TOKEN. Do not add pip dependencies.
"""
from __future__ import annotations

import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

KEY_RE = re.compile(r"[A-Z][A-Z0-9]+-[0-9]+")
ENV_KEYS = (
    "JIRA_BASE",
    "JIRA_BASE_URL",
    "JIRA_TOKEN",
    "JIRA_TOKEN_FILE",
    "JIRA_ENV_FILE",
)


def _load_kv_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        line = line.lstrip("\ufeff")
        if line.startswith("export "):
            line = line[7:].strip()
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip("\"'")
        if key in ENV_KEYS and not os.environ.get(key):
            os.environ[key] = val


def _token_candidates() -> list[Path]:
    if os.environ.get("JIRA_TOKEN_FILE"):
        return [Path(os.environ["JIRA_TOKEN_FILE"])]
    out: list[Path] = []
    home = os.environ.get("HOME")
    if home:
        out.append(Path(home) / ".config" / "atlassian" / "jira.token")
    profile = os.environ.get("USERPROFILE")
    if profile:
        out.append(Path(profile) / ".config" / "atlassian" / "jira.token")
    return out


def load_jira_env() -> None:
    env_file = os.environ.get("JIRA_ENV_FILE")
    if env_file:
        _load_kv_file(Path(env_file))
    def _walk(start: Path) -> None:
        cur = start.resolve()
        if cur.is_file():
            cur = cur.parent
        for _ in range(8):
            _load_kv_file(cur / ".env")
            if cur.parent == cur:
                break
            cur = cur.parent

    _walk(Path.cwd())
    _walk(Path(__file__).resolve().parent)
    if not os.environ.get("JIRA_TOKEN"):
        for candidate in _token_candidates():
            if candidate.is_file():
                os.environ["JIRA_TOKEN"] = candidate.read_text(encoding="utf-8").strip()
                break
    if not os.environ.get("JIRA_BASE") and os.environ.get("JIRA_BASE_URL"):
        os.environ["JIRA_BASE"] = os.environ["JIRA_BASE_URL"]
    if not os.environ.get("JIRA_BASE"):
        print("Missing JIRA_BASE (or JIRA_BASE_URL). Set it in the environment or .env.", file=sys.stderr)
        raise SystemExit(1)
    if not os.environ.get("JIRA_TOKEN"):
        print(
            "Missing Jira token. Put a Data Center PAT in ~/.config/atlassian/jira.token "
            "or %USERPROFILE%\\.config\\atlassian\\jira.token, or set JIRA_TOKEN / JIRA_ENV_FILE.",
            file=sys.stderr,
        )
        raise SystemExit(1)


def jira_url(path: str) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path
    base = os.environ.get("JIRA_BASE") or os.environ.get("JIRA_BASE_URL") or ""
    base = base.rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def parse_issue_key(raw: str) -> str:
    match = KEY_RE.search(raw)
    if not match:
        print(f"Could not parse issue key from: {raw}", file=sys.stderr)
        raise SystemExit(1)
    return match.group(0)


def dry_run(method: str, url: str, body: str = "") -> None:
    print(f"curl -sS -X {method} \\")
    print("  -H 'Authorization: Bearer ***' \\")
    if body:
        print("  -H 'Accept: application/json' \\")
        print("  -H 'Content-Type: application/json' \\")
        print(f"  --data {body} \\")
    else:
        print("  -H 'Accept: application/json' \\")
    print(f"  '{url}'")


def jira_request(method: str, path: str, body: str = "", *, dry: bool = False, echo: bool = True) -> str:
    load_jira_env()
    url = jira_url(path)
    if dry or os.environ.get("JIRA_DRY_RUN") == "1":
        dry_run(method, url, body)
        return ""
    data = body.encode("utf-8") if body else None
    headers = {
        "Authorization": f"Bearer {os.environ['JIRA_TOKEN']}",
        "Accept": "application/json",
    }
    if body:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, context=ssl.create_default_context()) as resp:
            payload = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace")
        print(f"Jira HTTP {exc.code} for {method} {url}", file=sys.stderr)
        print(err, file=sys.stderr)
        raise SystemExit(1) from exc
    except urllib.error.URLError as exc:
        print(f"Jira request failed: {exc.reason}", file=sys.stderr)
        raise SystemExit(1) from exc
    if echo:
        print(payload)
    return payload


def find_transition_id(want: str, payload: str) -> str:
    data = json.loads(payload)
    needle = want.strip().lower()
    for trans in data.get("transitions") or []:
        names = [trans.get("name") or "", (trans.get("to") or {}).get("name") or ""]
        if any(name.lower() == needle for name in names if name):
            return str(trans["id"])
    print(f"No transition matching {want!r}", file=sys.stderr)
    raise SystemExit(1)


def usage() -> None:
    print(
        """Usage:
  jira GET|POST|PUT|DELETE <path> [json-body]
  jira get <KEY-or-browse-URL>
  jira transitions <KEY-or-URL>
  jira transition <KEY-or-URL> <status-name>
  jira comment <KEY-or-URL> <text>
  jira create <PROJECT> <summary> [--type Task]
  jira parse-key <KEY-or-URL>
  jira find-transition <status-name>   # JSON on stdin

Prefix any command with --dry-run to print curl (token redacted).
Last-resort runner (stdlib). Prefer scripts/jira or jira.cmd."""
    )


def main(argv: list[str]) -> None:
    dry = False
    if argv and argv[0] == "--dry-run":
        dry = True
        argv = argv[1:]
    cmd = argv[0] if argv else ""
    rest = argv[1:]
    if cmd in ("", "-h", "--help", "help"):
        usage()
        return
    if cmd == "parse-key":
        print(parse_issue_key(rest[0] if rest else ""))
        return
    if cmd == "find-transition":
        print(find_transition_id(rest[0] if rest else "", sys.stdin.read()))
        return
    if cmd in ("GET", "POST", "PUT", "DELETE", "PATCH"):
        path = rest[0] if rest else ""
        body = rest[1] if len(rest) > 1 else ""
        jira_request(cmd, path, body, dry=dry)
        return
    if cmd == "get":
        key = parse_issue_key(rest[0] if rest else "")
        jira_request("GET", f"/rest/api/2/issue/{key}", dry=dry)
        return
    if cmd == "transitions":
        key = parse_issue_key(rest[0] if rest else "")
        jira_request("GET", f"/rest/api/2/issue/{key}/transitions", dry=dry)
        return
    if cmd == "transition":
        key = parse_issue_key(rest[0] if rest else "")
        status_name = rest[1] if len(rest) > 1 else ""
        if dry:
            jira_request("GET", f"/rest/api/2/issue/{key}/transitions", dry=True)
            print(
                f"# then POST /rest/api/2/issue/{key}/transitions "
                f"with the matching transition id for {status_name!r}"
            )
            return
        trans_json = jira_request(
            "GET", f"/rest/api/2/issue/{key}/transitions", echo=False
        )
        tid = find_transition_id(status_name, trans_json)
        jira_request(
            "POST",
            f"/rest/api/2/issue/{key}/transitions",
            json.dumps({"transition": {"id": tid}}),
        )
        print(f"Transitioned {key} -> {status_name} (id {tid})")
        return
    if cmd == "comment":
        key = parse_issue_key(rest[0] if rest else "")
        text = rest[1] if len(rest) > 1 else ""
        jira_request(
            "POST",
            f"/rest/api/2/issue/{key}/comment",
            json.dumps({"body": text}),
            dry=dry,
        )
        return
    if cmd == "create":
        project = rest[0] if rest else ""
        summary = rest[1] if len(rest) > 1 else ""
        itype = "Task"
        if len(rest) >= 4 and rest[2] == "--type":
            itype = rest[3]
        jira_request(
            "POST",
            "/rest/api/2/issue",
            json.dumps(
                {
                    "fields": {
                        "project": {"key": project},
                        "summary": summary,
                        "issuetype": {"name": itype},
                    }
                }
            ),
            dry=dry,
        )
        return
    print(f"Unknown command: {cmd}", file=sys.stderr)
    usage()
    raise SystemExit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
