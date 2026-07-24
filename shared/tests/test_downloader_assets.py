from __future__ import annotations

import sys
from pathlib import Path

import jsonschema
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from shared.downloader_assets import DOWNLOADER_LICENSE, build_assets_manifest  # noqa: E402
from shared.envelopes import validate_against_schema  # noqa: E402


def _manifest(*clips: tuple[str, float | None]) -> dict:
    return {
        "stage": "01_1_downloader",
        "clip_count": len(clips),
        "clips": [
            {"clip_id": cid, "file_ref": f"shared/runs/r/cache/downloader_clips/s/{cid}.mp4", "duration_s": dur}
            for cid, dur in clips
        ],
    }


def _scores(*beats: tuple[str, list[tuple[str, float]]]) -> dict:
    scores_by_beat = []
    for beat_id, ranked in beats:
        ranked_clips = [
            {"clip_id": cid, "file_ref": f"shared/runs/r/cache/downloader_clips/s/{cid}.mp4",
             "score": score, "rank": i + 1, "frames_scored": 3}
            for i, (cid, score) in enumerate(ranked)
        ]
        scores_by_beat.append({"beat_id": beat_id, "ranked_clips": ranked_clips})
    return {"run_id": "r", "scene_id": "s", "scores_by_beat": scores_by_beat}


def test_builds_source_free_top_n_assets():
    manifest = _manifest(("clip_001", 197.23), ("clip_002", 89.88), ("clip_003", 118.9))
    scores = _scores(
        ("b001", [("clip_001", 0.33), ("clip_003", 0.31), ("clip_002", 0.25)]),
        ("b002", [("clip_003", 0.30), ("clip_001", 0.28)]),
    )

    out = build_assets_manifest(scores, manifest, "r", "s", assets_per_beat=2)

    # 2 assets for b001 (top-2 of 3), 2 for b002.
    b001 = [a for a in out["assets"] if a["beat_id"] == "b001"]
    b002 = [a for a in out["assets"] if a["beat_id"] == "b002"]
    assert len(b001) == 2 and len(b002) == 2

    primary = b001[0]
    assert primary["asset_id"] == "b001__clip_001"
    assert primary["origin"] == "downloader"
    assert primary["duration_s"] == 197.23  # pulled from the downloader manifest
    assert primary["rank"] == 1
    assert primary["confidence"] == 0.33
    # Source-free: neutral license, lane-labelled source, no creator ever.
    assert primary["license"] == DOWNLOADER_LICENSE
    assert primary["attribution"] == {"source": "downloader", "creator_required": False}
    assert "creator" not in primary["attribution"]

    # Output is schema-valid (build_assets_manifest also validates internally).
    validate_against_schema(out, "assets_manifest.schema.json")


def test_assets_per_beat_limits_selection():
    manifest = _manifest(("clip_001", 10.0), ("clip_002", 20.0), ("clip_003", 30.0))
    scores = _scores(("b1", [("clip_001", 0.4), ("clip_002", 0.3), ("clip_003", 0.2)]))

    out = build_assets_manifest(scores, manifest, "r", "s", assets_per_beat=1)

    assert len(out["assets"]) == 1
    assert out["assets"][0]["asset_id"] == "b1__clip_001"


def test_clip_without_usable_duration_is_skipped():
    # clip_002 has no duration in the manifest -> can't form a valid asset.
    manifest = _manifest(("clip_001", 10.0), ("clip_002", None), ("clip_003", 0.0))
    scores = _scores(("b1", [("clip_001", 0.4), ("clip_002", 0.3), ("clip_003", 0.2)]))

    out = build_assets_manifest(scores, manifest, "r", "s", assets_per_beat=3)

    ids = {a["asset_id"] for a in out["assets"]}
    assert ids == {"b1__clip_001"}  # clip_002 (None) and clip_003 (0.0) skipped


def test_negative_score_clamped_to_zero_confidence():
    manifest = _manifest(("clip_001", 10.0))
    scores = _scores(("b1", [("clip_001", -0.12)]))

    out = build_assets_manifest(scores, manifest, "r", "s", assets_per_beat=3)

    assert out["assets"][0]["confidence"] == 0.0  # min(1, max(0, -0.12))
    validate_against_schema(out, "assets_manifest.schema.json")


def test_empty_ranking_yields_no_assets_for_that_beat():
    manifest = _manifest(("clip_001", 10.0))
    scores = _scores(("b1", [("clip_001", 0.4)]), ("b2", []))

    out = build_assets_manifest(scores, manifest, "r", "s", assets_per_beat=3)

    assert {a["beat_id"] for a in out["assets"]} == {"b1"}


def test_same_clip_across_beats_gets_distinct_asset_ids():
    manifest = _manifest(("clip_001", 10.0))
    scores = _scores(("b1", [("clip_001", 0.4)]), ("b2", [("clip_001", 0.35)]))

    out = build_assets_manifest(scores, manifest, "r", "s", assets_per_beat=3)

    asset_ids = sorted(a["asset_id"] for a in out["assets"])
    assert asset_ids == ["b1__clip_001", "b2__clip_001"]


def test_all_clips_missing_duration_produces_empty_but_valid_manifest():
    manifest = _manifest(("clip_001", None))
    scores = _scores(("b1", [("clip_001", 0.4)]))

    out = build_assets_manifest(scores, manifest, "r", "s", assets_per_beat=3)

    assert out["assets"] == []
    # An empty assets array is still schema-valid.
    validate_against_schema(out, "assets_manifest.schema.json")
    with pytest.raises(jsonschema.ValidationError):
        # sanity: the schema really would reject a malformed asset
        validate_against_schema({"run_id": "r", "scene_id": "s", "assets": [{"beat_id": "b1"}]}, "assets_manifest.schema.json")
