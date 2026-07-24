from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np

# See stage 01's test_run.py for why this isn't a plain "import run".
STAGE_SRC = Path(__file__).resolve().parents[1] / "src"
_spec = importlib.util.spec_from_file_location("stage01_2_scene_scoring_run", STAGE_SRC / "run.py")
run = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = run
_spec.loader.exec_module(run)

THRESHOLDS = {"scene_scoring": {"frames_per_clip": 3}}

# 2-D vectors; cosine_similarity normalizes, so magnitudes are free.
TEXT_VECS = {
    "crow drops stone into bottle": np.array([1.0, 0.0]),
    "bird flies across the sky": np.array([0.0, 1.0]),
}
IMG_VECS = {
    "clip_001": np.array([1.0, 0.0]),   # aligns with beat 1
    "clip_002": np.array([0.0, 1.0]),   # aligns with beat 2
    "clip_003": np.array([1.0, 1.0]),   # middle for both
}


class FakeEmbedder:
    def embed_text(self, text: str) -> np.ndarray:
        return TEXT_VECS[text]

    def embed_image_bytes(self, image_bytes: bytes) -> np.ndarray:
        return IMG_VECS[image_bytes.decode("utf-8")]


def make_fake_extractor(fail_stems: set[str] | None = None, calls: list[int] | None = None):
    """Returns a frame extractor that writes n_frames tiny files, each
    containing the clip's stem so FakeEmbedder can map them to a vector."""
    fail_stems = fail_stems or set()

    def _extract(video_path: Path, out_dir: Path, n_frames: int) -> list[Path]:
        if calls is not None:
            calls.append(n_frames)
        if video_path.stem in fail_stems:
            raise run.FFmpegError(f"simulated extraction failure for {video_path.stem}")
        out_dir.mkdir(parents=True, exist_ok=True)
        token = video_path.stem.encode("utf-8")
        paths = []
        for i in range(n_frames):
            p = out_dir / f"frame_{i:02d}.jpg"
            p.write_bytes(token)
            paths.append(p)
        return paths

    return _extract


def _beats(descriptions: dict[str, str]) -> dict:
    return {
        "run_id": "test_run_01_2",
        "scene_id": "ch1_sc1",
        "beats": [
            {
                "beat_id": bid,
                "order": i,
                "text_excerpt_ref": f"para:{i + 1}",
                "visual_description": desc,
                "est_duration_s": 3.0,
                "mood_tags": ["quiet"],
                "no_visual_analog": False,
            }
            for i, (bid, desc) in enumerate(descriptions.items())
        ],
    }


def _manifest(clip_ids: list[str]) -> dict:
    return {
        "stage": "01_1_downloader",
        "generated_at": "2026-07-23T00:00:00+00:00",
        "clip_count": len(clip_ids),
        "clips": [
            {"clip_id": cid, "file_ref": f"clips/{cid}.mp4", "duration_s": 10.0}
            for cid in clip_ids
        ],
    }


def _write(input_dir: Path, beats: dict, manifest: dict, real_clip_ids: list[str]) -> None:
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "beats.json").write_text(json.dumps(beats), encoding="utf-8")
    (input_dir / "downloader_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    # Stage resolves file_ref basename against inputs/; create the ones that exist.
    for cid in real_clip_ids:
        (input_dir / f"{cid}.mp4").write_bytes(b"fake")


def test_ranks_clips_per_beat_best_fit_first(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    beats = _beats({"b1": "crow drops stone into bottle", "b2": "bird flies across the sky"})
    clips = ["clip_001", "clip_002", "clip_003"]
    _write(input_dir, beats, _manifest(clips), clips)

    response = run.main(
        input_dir, output_dir, {"run_id": "test_run_01_2"},
        frame_extractor=make_fake_extractor(), embedder=FakeEmbedder(), thresholds=THRESHOLDS,
    )

    assert response.status.value == "COMPLETE"
    out = json.loads((output_dir / "scene_scores.json").read_text(encoding="utf-8"))
    by_beat = {b["beat_id"]: b["ranked_clips"] for b in out["scores_by_beat"]}

    # b1 aligns with clip_001, then the middle clip_003, then clip_002.
    assert [c["clip_id"] for c in by_beat["b1"]] == ["clip_001", "clip_003", "clip_002"]
    # b2 aligns with clip_002, then clip_003, then clip_001.
    assert [c["clip_id"] for c in by_beat["b2"]] == ["clip_002", "clip_003", "clip_001"]
    # Ranks are a contiguous 1..N ordered by non-increasing score.
    for ranked in by_beat.values():
        assert [c["rank"] for c in ranked] == [1, 2, 3]
        scores = [c["score"] for c in ranked]
        assert scores == sorted(scores, reverse=True)
        assert all(c["frames_scored"] == 3 for c in ranked)
    # Source-free: no origin/source/url/license leaked into any clip entry.
    for ranked in by_beat.values():
        for c in ranked:
            assert set(c) == {"clip_id", "file_ref", "score", "rank", "frames_scored"}


def test_frames_per_clip_read_from_config(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    beats = _beats({"b1": "crow drops stone into bottle"})
    _write(input_dir, beats, _manifest(["clip_001"]), ["clip_001"])
    calls: list[int] = []

    run.main(
        input_dir, output_dir, {"run_id": "test_run_01_2"},
        frame_extractor=make_fake_extractor(calls=calls), embedder=FakeEmbedder(),
        thresholds={"scene_scoring": {"frames_per_clip": 2}},
    )

    assert calls == [2]  # extractor invoked once for the one clip, with the configured n


def test_extraction_failure_excludes_and_counts_clip(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    beats = _beats({"b1": "crow drops stone into bottle"})
    clips = ["clip_001", "clip_002"]
    _write(input_dir, beats, _manifest(clips), clips)

    response = run.main(
        input_dir, output_dir, {"run_id": "test_run_01_2"},
        frame_extractor=make_fake_extractor(fail_stems={"clip_002"}), embedder=FakeEmbedder(), thresholds=THRESHOLDS,
    )

    assert response.status.value == "COMPLETE"
    assert "1 clip(s) could not be frame-sampled" in response.summary
    out = json.loads((output_dir / "scene_scores.json").read_text(encoding="utf-8"))
    ranked = out["scores_by_beat"][0]["ranked_clips"]
    assert [c["clip_id"] for c in ranked] == ["clip_001"]


def test_missing_clip_file_excluded(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    beats = _beats({"b1": "crow drops stone into bottle"})
    # Manifest lists two clips but only clip_001 actually exists on disk.
    _write(input_dir, beats, _manifest(["clip_001", "clip_404"]), ["clip_001"])

    response = run.main(
        input_dir, output_dir, {"run_id": "test_run_01_2"},
        frame_extractor=make_fake_extractor(), embedder=FakeEmbedder(), thresholds=THRESHOLDS,
    )

    assert response.status.value == "COMPLETE"
    out = json.loads((output_dir / "scene_scores.json").read_text(encoding="utf-8"))
    assert [c["clip_id"] for c in out["scores_by_beat"][0]["ranked_clips"]] == ["clip_001"]


def test_missing_input_files_fails(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    input_dir.mkdir()

    response = run.main(
        input_dir, output_dir, {"run_id": "test_run_01_2"},
        frame_extractor=make_fake_extractor(), embedder=FakeEmbedder(), thresholds=THRESHOLDS,
    )

    assert response.status.value == "FAILED"


def test_empty_clips_yields_empty_rankings(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    beats = _beats({"b1": "crow drops stone into bottle"})
    _write(input_dir, beats, _manifest([]), [])

    response = run.main(
        input_dir, output_dir, {"run_id": "test_run_01_2"},
        frame_extractor=make_fake_extractor(), embedder=FakeEmbedder(), thresholds=THRESHOLDS,
    )

    assert response.status.value == "COMPLETE"
    out = json.loads((output_dir / "scene_scores.json").read_text(encoding="utf-8"))
    assert out["scores_by_beat"][0]["ranked_clips"] == []


def test_empty_beats_yields_empty_output(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    beats = _beats({})
    _write(input_dir, beats, _manifest(["clip_001"]), ["clip_001"])

    response = run.main(
        input_dir, output_dir, {"run_id": "test_run_01_2"},
        frame_extractor=make_fake_extractor(), embedder=FakeEmbedder(), thresholds=THRESHOLDS,
    )

    assert response.status.value == "COMPLETE"
    out = json.loads((output_dir / "scene_scores.json").read_text(encoding="utf-8"))
    assert out["scores_by_beat"] == []
