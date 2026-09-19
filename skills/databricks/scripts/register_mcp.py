#!/usr/bin/env python3
"""Add this skill's tools to a FastMCP server's mcp_tools.yaml.

    python register_mcp.py <path/to/mcp_tools.yaml>

- Inserts the entries from ../mcp_tools.databricks.yaml at the end of the `tools:` list,
  with the list's own indentation. The rest of the file keeps its text and comments.
- Sets each `script:` to this skill's dbx_mcp.py, relative to the YAML file's folder.
- Replaces entries with the same names, so a second run updates them.
- Checks that the result parses and that every script path exists before it writes.
- Keeps the file from before the first run as mcp_tools.yaml.bak.

Needs PyYAML, which the MCP server already uses.
"""

import argparse
import os
import re
import sys
from pathlib import Path

import yaml

SKILL = Path(__file__).resolve().parent.parent
SNIPPET = SKILL / "mcp_tools.databricks.yaml"
ADAPTER = SKILL / "scripts" / "dbx_mcp.py"


def entry_blocks(snippet: str) -> list[tuple[str, list[str]]]:
    """(name, lines) for each `- name:` entry in the snippet, without comments or blank lines."""
    blocks: list[tuple[str, list[str]]] = []
    for line in snippet.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"- name:\s*(\S+)", line)
        if m:
            blocks.append((m.group(1), [line]))
        elif blocks:
            blocks[-1][1].append(line)
    return blocks


def tools_span(lines: list[str]) -> tuple[int, int, int]:
    """(first line after `tools:`, end of the tools block, item indent)."""
    start = next((i for i, ln in enumerate(lines) if re.match(r"tools:\s*(#.*)?$", ln)), None)
    if start is None:
        raise SystemExit("No top-level `tools:` key in the file.")
    indent = None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        ln = lines[i]
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        if indent is None and ln.lstrip().startswith("- "):
            indent = len(ln) - len(ln.lstrip())
        # A top-level key ends the list. Items at column 0 start with "-".
        if not ln[0].isspace() and not ln.startswith("-"):
            end = i
            break
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return start + 1, end, 2 if indent is None else indent


def remove_entries(lines: list[str], start: int, end: int, indent: int, names: set[str]) -> list[str]:
    out = lines[:start]
    skipping = False
    item = re.compile(r" {%d}- " % indent)
    for ln in lines[start:end]:
        if item.match(ln):
            m = re.match(r"\s*- name:\s*(\S+)", ln)
            skipping = bool(m and m.group(1) in names)
        if not skipping:
            out.append(ln)
    return out + lines[end:]


def register(config: Path) -> list[str]:
    text = config.read_bytes().decode("utf-8")  # read_text() would turn CRLF into LF
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    script = Path(os.path.relpath(ADAPTER, config.resolve().parent)).as_posix()
    blocks = entry_blocks(SNIPPET.read_text(encoding="utf-8"))
    names = {n for n, _ in blocks}

    start, end, indent = tools_span(lines)
    lines = remove_entries(lines, start, end, indent, names)
    start, end, indent = tools_span(lines)

    new: list[str] = []
    for _, block in blocks:
        for ln in block:
            ln = re.sub(r"^(\s*script:\s*).*$", lambda m: m.group(1) + script, ln)
            new.append(" " * indent + ln)
    lines[end:end] = new
    result = newline.join(lines) + newline

    data = yaml.safe_load(result)
    got = {t["name"]: t for t in data.get("tools", [])}
    for n in names:
        if n not in got:
            raise SystemExit(f"Check failed: {n} is not in the new tools list. Nothing written.")
        if not (config.parent / got[n]["script"]).resolve().is_file():
            raise SystemExit(f"Check failed: script for {n} does not exist. Nothing written.")

    backup = config.with_name(config.name + ".bak")
    if not backup.exists():  # keep the file as it was before the first run
        backup.write_text(text, encoding="utf-8", newline="")
    config.write_text(result, encoding="utf-8", newline="")
    return sorted(names)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("config", type=Path, help="Path to the server's mcp_tools.yaml")
    a = ap.parse_args()
    names = register(a.config)
    print(f"Registered {len(names)} tools in {a.config}: {', '.join(names)}")
    print("Restart the MCP server to load them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
