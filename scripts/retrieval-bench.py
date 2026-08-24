#!/usr/bin/env python3
"""Repo-root launcher for skills/retrieval-bench. Works on Windows (py -3)."""

from __future__ import annotations

import runpy
from pathlib import Path

CLI = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "retrieval-bench"
    / "scripts"
    / "retrieval_bench.py"
)
runpy.run_path(str(CLI), run_name="__main__")
