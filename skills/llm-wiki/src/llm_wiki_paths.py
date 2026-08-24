"""Resolve skill root, raw/, and wiki/. Env overrides keep tests off the skill folder."""

from __future__ import annotations

import os
from pathlib import Path


def skill_root() -> Path:
    raw = os.environ.get("LLM_WIKI_ROOT")
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[1]


def raw_dir() -> Path:
    override = os.environ.get("LLM_WIKI_RAW")
    if override:
        return Path(override)
    return skill_root() / "raw"


def wiki_dir() -> Path:
    override = os.environ.get("LLM_WIKI_WIKI")
    if override:
        return Path(override)
    return skill_root() / "wiki"
