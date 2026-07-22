from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

# See stage 01's test_run.py for why this isn't a plain "import run".
STAGE_SRC = Path(__file__).resolve().parents[1] / "src"
_spec = importlib.util.spec_from_file_location("stage12_qa_attribution_run", STAGE_SRC / "run.py")
run = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = run
_spec.loader.exec_module(run)

RUN_CONFIG = {"run_id": "test_run_12"}
THRESHOLDS = {"qa": {"duration_tolerance_pct": 2}}
AUDIO_SPEC = {"loudness": {"target_lufs": -16.0, "tolerance_lu": 1.0}}


def _valid_artifacts() -> dict[str, dict]:
    return {
        "beats.json": {
            "run_id": "test_run_12", "scene_id": "s1",
            "beats": [{"beat_id": "b1", "order": 0, "text_excerpt_ref": "para:1", "visual_description": "d", "est_duration_s": 3.0, "mood_tags": ["quiet"], "no_visual_analog": False}],
        },
        "candidates.json": {
            "run_id": "test_run_12", "scene_id": "s1",
            "candidates_by_beat": [{"beat_id": "b1", "search_terms": ["x"], "candidates": []}],
        },
        "assets_manifest.json": {
            "run_id": "test_run_12", "scene_id": "s1",
            "assets": [{"beat_id": "b1", "asset_id": "a1", "origin": "retrieved_verified", "file_ref": "f.mp4", "duration_s": 3.0, "license": "Pexels License", "attribution": {"source": "pexels", "creator_required": False}}],
        },
        "edit_plan.json": {
            "run_id": "test_run_12", "scene_id": "s1", "total_runtime_s": 3.0,
            "beats": [{"beat_id": "b1", "asset_id": "a1", "shots": [{"shot_id": "b1_s1", "in_s": 0.0, "out_s": 3.0, "hold_duration_s": 3.0}], "transition_out": "hard-cut", "rationale": ""}],
        },
        "timeline.json": {
            "run_id": "test_run_12", "scene_id": "s1",
            "clips": [{"shot_id": "b1_s1", "file_ref": "f.mp4", "source_in_s": 0.0, "source_out_s": 3.0, "timeline_start_s": 0.0, "timeline_end_s": 3.0}],
            "total_duration_s": 3.0,
        },
        "music_cue_intent.json": {
            "run_id": "test_run_12", "scene_id": "s1",
            "cues": [{"cue_id": "c1", "start_beat_id": "b1", "end_beat_id": "b1", "mood_tags": ["quiet"], "target_intensity": 0.3, "rationale": "r"}],
        },
        "audio_mix.json": {
            "run_id": "test_run_12", "scene_id": "s1",
            "narration_stems": [{"beat_id": "b1", "file_ref": "n.wav", "start_s": 0.0, "duration_s": 3.0}],
            "music_stems": [{"cue_id": "c1", "track_ref": "t1", "file_ref": "m.mp3", "start_s": 0.0, "duration_s": 3.0, "selected_by": "human"}],
            "mix_params": {"ducking_depth_db": -12, "ducking_attack_ms": 150},
            "final_lufs": -16.0,
            "total_duration_s": 3.0,
        },
        "manifest.json": {
            "run_id": "test_run_12",
            "entries": [
                {"entry_id": "a1", "kind": "footage", "fetched_by_stage": "03_candidate_fetch", "fetched_at": "2026-07-15T00:00:00Z", "source": "pexels", "license": "Pexels License", "attribution_required": False},
                {"entry_id": "t1", "kind": "music", "fetched_by_stage": "09_audio_production", "fetched_at": "2026-07-15T00:00:00Z", "source": "mixkit", "creator": "Some Artist", "license": "Mixkit License", "attribution_required": False},
            ],
        },
    }


def _make_video(dest: Path, duration: float) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=black:s=64x36:d={duration}:r=10", "-pix_fmt", "yuv420p", str(dest)],
        capture_output=True, text=True, check=True,
    )


def _write(input_dir: Path, artifacts: dict[str, dict], final_duration: float = 3.0) -> None:
    input_dir.mkdir(parents=True, exist_ok=True)
    for filename, data in artifacts.items():
        (input_dir / filename).write_text(json.dumps(data), encoding="utf-8")
    _make_video(input_dir / "final.mp4", final_duration)


def test_all_checks_pass(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    _write(input_dir, _valid_artifacts(), final_duration=3.0)

    response = run.main(input_dir, output_dir, RUN_CONFIG, thresholds=THRESHOLDS, audio_spec=AUDIO_SPEC)

    assert response.status.value == "COMPLETE"
    report = json.loads((output_dir / "qa_report.json").read_text(encoding="utf-8"))
    assert report["pass"] is True
    assert all(c["pass"] for c in report["checks"])
    credits = (output_dir / "CREDITS.md").read_text(encoding="utf-8")
    assert "Footage" in credits
    assert "Music" in credits
    assert "Some Artist" in credits


def test_missing_artifact_fails_schema_check(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    artifacts = _valid_artifacts()
    del artifacts["candidates.json"]
    _write(input_dir, artifacts, final_duration=3.0)

    response = run.main(input_dir, output_dir, RUN_CONFIG, thresholds=THRESHOLDS, audio_spec=AUDIO_SPEC)

    assert response.status.value == "FAILED"
    report = json.loads((output_dir / "qa_report.json").read_text(encoding="utf-8"))
    assert report["pass"] is False
    schema_check = next(c for c in report["checks"] if c["name"] == "schema_validation")
    assert schema_check["pass"] is False
    assert "candidates.json" in schema_check["detail"]


def test_attribution_missing_creator_fails(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    artifacts = _valid_artifacts()
    artifacts["manifest.json"]["entries"][0]["attribution_required"] = True  # no creator field set
    _write(input_dir, artifacts, final_duration=3.0)

    response = run.main(input_dir, output_dir, RUN_CONFIG, thresholds=THRESHOLDS, audio_spec=AUDIO_SPEC)

    assert response.status.value == "FAILED"
    report = json.loads((output_dir / "qa_report.json").read_text(encoding="utf-8"))
    attribution_check = next(c for c in report["checks"] if c["name"] == "attribution_completeness")
    assert attribution_check["pass"] is False


def test_duration_drift_fails(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    _write(input_dir, _valid_artifacts(), final_duration=10.0)  # target is 3.0s

    response = run.main(input_dir, output_dir, RUN_CONFIG, thresholds=THRESHOLDS, audio_spec=AUDIO_SPEC)

    assert response.status.value == "FAILED"
    report = json.loads((output_dir / "qa_report.json").read_text(encoding="utf-8"))
    duration_check = next(c for c in report["checks"] if c["name"] == "duration_tolerance")
    assert duration_check["pass"] is False


def test_loudness_drift_fails(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    artifacts = _valid_artifacts()
    artifacts["audio_mix.json"]["final_lufs"] = -10.0  # target -16.0, tolerance 1.0
    _write(input_dir, artifacts, final_duration=3.0)

    response = run.main(input_dir, output_dir, RUN_CONFIG, thresholds=THRESHOLDS, audio_spec=AUDIO_SPEC)

    assert response.status.value == "FAILED"
    report = json.loads((output_dir / "qa_report.json").read_text(encoding="utf-8"))
    loudness_check = next(c for c in report["checks"] if c["name"] == "loudness_spec")
    assert loudness_check["pass"] is False


def test_missing_final_mp4_fails_hard(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    input_dir.mkdir()

    response = run.main(input_dir, output_dir, RUN_CONFIG, thresholds=THRESHOLDS, audio_spec=AUDIO_SPEC)

    assert response.status.value == "FAILED"
    assert not (output_dir / "qa_report.json").exists()
