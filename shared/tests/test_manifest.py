import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from shared.manifest import append_manifest_entries  # noqa: E402

RUN_ID = "test_manifest_dedup"
RUN_DIR = REPO_ROOT / "shared" / "runs" / RUN_ID


def setup_function() -> None:
    if RUN_DIR.exists():
        shutil.rmtree(RUN_DIR)


def teardown_function() -> None:
    if RUN_DIR.exists():
        shutil.rmtree(RUN_DIR)


def _entry(entry_id: str, license_: str = "Pexels License") -> dict:
    return {
        "entry_id": entry_id,
        "kind": "footage",
        "fetched_by_stage": "03_candidate_fetch",
        "fetched_at": "2026-07-15T00:00:00Z",
        "source": "pexels",
        "license": license_,
        "attribution_required": False,
    }


def test_repeated_calls_with_same_entry_id_do_not_duplicate():
    # Regression test: a real run's manifest.json ended up with the same
    # entry_id three times because Stage 05/09 were each re-run multiple
    # times during debugging/recovery - append_manifest_entries only
    # de-duplicated within a single call's own entries, not against what
    # was already on disk from an earlier call.
    append_manifest_entries(RUN_DIR, RUN_ID, [_entry("pexels_1")])
    append_manifest_entries(RUN_DIR, RUN_ID, [_entry("pexels_1")])
    append_manifest_entries(RUN_DIR, RUN_ID, [_entry("pexels_1")])

    manifest = json.loads((RUN_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["entries"]) == 1


def test_re_fetch_with_updated_metadata_overwrites_not_duplicates():
    append_manifest_entries(RUN_DIR, RUN_ID, [_entry("asset_1", license_="Old License Text")])
    append_manifest_entries(RUN_DIR, RUN_ID, [_entry("asset_1", license_="Corrected License Text")])

    manifest = json.loads((RUN_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["entries"]) == 1
    assert manifest["entries"][0]["license"] == "Corrected License Text"


def test_distinct_entry_ids_both_kept():
    append_manifest_entries(RUN_DIR, RUN_ID, [_entry("asset_1")])
    append_manifest_entries(RUN_DIR, RUN_ID, [_entry("asset_2")])

    manifest = json.loads((RUN_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert {e["entry_id"] for e in manifest["entries"]} == {"asset_1", "asset_2"}
