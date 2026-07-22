from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# See stage 01's test_run.py for why this isn't a plain "import run".
STAGE_SRC = Path(__file__).resolve().parents[1] / "src"
_spec = importlib.util.spec_from_file_location("stage10_human_review_gate_run", STAGE_SRC / "run.py")
run = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = run
_spec.loader.exec_module(run)

REPO_ROOT = Path(__file__).resolve().parents[3]
RUN_CONFIG = {"run_id": "test_run_10"}


@pytest.fixture(autouse=True)
def _clean_run_cache():
    # See stage 09's test_run.py for why this matters: every test shares
    # run_id "test_run_10" and shot_ids, and main() skips re-extracting a
    # thumbnail if the cached file already exists - without cleaning up,
    # one test's cached thumbnail silently short-circuits another test's
    # fake/failing extractor.
    run_dir = REPO_ROOT / "shared" / "runs" / "test_run_10"
    if run_dir.exists():
        shutil.rmtree(run_dir)
    yield
    if run_dir.exists():
        shutil.rmtree(run_dir)


def _timeline() -> dict:
    return {
        "run_id": "test_run_10",
        "scene_id": "ch1_sc1",
        "clips": [
            {
                "shot_id": "b1_s1",
                "file_ref": "cache/videos/asset1.mp4",
                "source_in_s": 0.0,
                "source_out_s": 3.0,
                "timeline_start_s": 0.0,
                "timeline_end_s": 3.0,
                "transition_out": {"type": "crossfade", "duration_s": 0.0},
            },
            {
                "shot_id": "b2_s1",
                "file_ref": "cache/videos/asset2.mp4",
                "source_in_s": 0.0,
                "source_out_s": 4.0,
                "timeline_start_s": 3.0,
                "timeline_end_s": 7.0,
            },
        ],
        "total_duration_s": 7.0,
    }


def _audio_mix() -> dict:
    return {
        "run_id": "test_run_10",
        "scene_id": "ch1_sc1",
        "narration_stems": [
            {"beat_id": "b1", "file_ref": "cache/narration/b1.wav", "start_s": 0.0, "duration_s": 3.0},
            {"beat_id": "b2", "file_ref": "cache/narration/b2.wav", "start_s": 3.0, "duration_s": 4.0},
        ],
        "music_stems": [
            {"cue_id": "cue001", "track_ref": "mixkit_671", "file_ref": "cache/music/cue001.mp3", "start_s": 0.0, "duration_s": 7.0, "selected_by": "human"}
        ],
        "mix_params": {"ducking_depth_db": -12, "ducking_attack_ms": 150},
        "final_lufs": -16.0,
    }


def _write(input_dir: Path, timeline: dict, audio_mix: dict) -> None:
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "timeline.json").write_text(json.dumps(timeline), encoding="utf-8")
    (input_dir / "audio_mix.json").write_text(json.dumps(audio_mix), encoding="utf-8")


def _fake_thumbnail_extractor(video_path: Path, timestamp_s: float, dest_path: Path) -> None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=64x36:d=0.1", "-frames:v", "1", str(dest_path)],
        capture_output=True,
        text=True,
        check=True,
    )


def test_complete_happy_path(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    _write(input_dir, _timeline(), _audio_mix())

    response = run.main(input_dir, output_dir, RUN_CONFIG, thumbnail_extractor=_fake_thumbnail_extractor)

    assert response.status.value == "COMPLETE"
    html = (output_dir / "contact_sheet.html").read_text(encoding="utf-8")
    assert "b1_s1" in html
    assert "b2_s1" in html
    assert "mixkit_671" in html
    assert "crossfade" in html
    assert "data:image/jpeg;base64," in html


def test_thumbnail_failure_fails(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    _write(input_dir, _timeline(), _audio_mix())

    def _raising_extractor(video_path, timestamp_s, dest_path):
        raise run.FFmpegError("simulated extraction failure")

    response = run.main(input_dir, output_dir, RUN_CONFIG, thumbnail_extractor=_raising_extractor)

    assert response.status.value == "FAILED"
    assert not (output_dir / "contact_sheet.html").exists()


def test_missing_input_files_fails(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    input_dir.mkdir()

    response = run.main(input_dir, output_dir, RUN_CONFIG)

    assert response.status.value == "FAILED"


def test_html_escapes_untrusted_content(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    timeline = _timeline()
    timeline["clips"][0]["shot_id"] = "<script>alert(1)</script>"
    audio_mix = _audio_mix()
    _write(input_dir, timeline, audio_mix)

    response = run.main(input_dir, output_dir, RUN_CONFIG, thumbnail_extractor=_fake_thumbnail_extractor)

    assert response.status.value == "COMPLETE"
    html = (output_dir / "contact_sheet.html").read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_no_music_stems_renders_without_crash(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    audio_mix = _audio_mix()
    audio_mix["music_stems"] = []
    _write(input_dir, _timeline(), audio_mix)

    response = run.main(input_dir, output_dir, RUN_CONFIG, thumbnail_extractor=_fake_thumbnail_extractor)

    assert response.status.value == "COMPLETE"
    html = (output_dir / "contact_sheet.html").read_text(encoding="utf-8")
    assert "Music (0)" in html
