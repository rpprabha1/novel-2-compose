#!/usr/bin/env python3
"""Thin CLI shim over the Coordinator (stages/00_coordinator).

The multi-scene orchestration that used to live here now lives in the real
Coordinator at `stages/00_coordinator/src/run.py` (CLAUDE.md sections 2/5 - the
hub-and-spoke component that invokes stages, validates every Stage Response
against its expected_output_schema, enforces gates, and logs every envelope +
response to shared/runs/<run_id>/coordinator_log.jsonl). This file remains only
as the familiar repo-root entrypoint; it loads that module and delegates.

Run:  python run_full_novel.py [scene_id ...]
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent


def _load_coordinator():
    path = REPO_ROOT / "stages" / "00_coordinator" / "src" / "run.py"
    spec = importlib.util.spec_from_file_location("coordinator", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(scene_ids: list[str] | None = None) -> int:
    return _load_coordinator().main(scene_ids)


if __name__ == "__main__":
    scene_arg = sys.argv[1:] if len(sys.argv) > 1 else None
    sys.exit(main(scene_arg))
