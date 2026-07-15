"""Appends to shared/runs/<run_id>/manifest.json - the authoritative attribution
record every fetching stage writes to at fetch time (CLAUDE.md rule 12)."""

from __future__ import annotations

import json
from pathlib import Path

from .envelopes import validate_against_schema


def append_manifest_entries(run_dir: Path, run_id: str, entries: list[dict]) -> None:
    if not entries:
        return
    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {"run_id": run_id, "entries": []}
    manifest["entries"].extend(entries)
    validate_against_schema(manifest, "manifest.schema.json")
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
