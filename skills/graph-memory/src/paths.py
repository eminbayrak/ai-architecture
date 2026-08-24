"""Resolve skill root and graph.db. Env overrides exist so tests do not write into the skill folder."""

from __future__ import annotations

import os
from pathlib import Path


def skill_root() -> Path:
    raw = os.environ.get("GRAPH_MEMORY_ROOT")
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[1]


def db_path() -> Path:
    raw = os.environ.get("GRAPH_MEMORY_DB")
    if raw:
        return Path(raw)
    return skill_root() / "graph.db"
