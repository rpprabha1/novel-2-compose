"""Appends to shared/runs/<run_id>/manifest.json - the authoritative attribution
record every fetching stage writes to at fetch time (CLAUDE.md rule 12)."""

from __future__ import annotations

import json
from pathlib import Path

from .envelopes import validate_against_schema


def append_manifest_entries(run_dir: Path, run_id: str, entries: list[dict]) -> None:
    """De-duplicates by entry_id against what's already on disk, not just
    within this call's own entries - a stage re-run (e.g. recovering from a
    cleared cache, or re-fetching after a fix) would otherwise pile up true
    duplicates in CREDITS.md every time. Last write for a given entry_id
    wins, since a re-fetch may carry corrected metadata (e.g. a regenerated
    fallback asset's real duration)."""
    if not entries:
        return
    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {"run_id": run_id, "entries": []}

    by_id = {e["entry_id"]: e for e in manifest["entries"]}
    for entry in entries:
        by_id[entry["entry_id"]] = entry
    manifest["entries"] = list(by_id.values())

    validate_against_schema(manifest, "manifest.schema.json")
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
