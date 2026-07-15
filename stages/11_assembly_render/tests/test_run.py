import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# See stage 01's test_run.py for why this isn't a plain "import run".
STAGE_SRC = Path(__file__).resolve().parents[1] / "src"
_spec = importlib.util.spec_from_file_location("stage11_assembly_render_run", STAGE_SRC / "run.py")
run = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = run
_spec.loader.exec_module(run)

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

RUN_CONFIG = {"run_id": "test_run_11"}


@pytest.fixture(autouse=True)
def _clean_run_cache():
    # See stage 09/10's test_run.py: every test shares run_id "test_run_11"
    # and several reuse shot_ids ("s1"/"s2") with different clip content -
    # main() caches normalized clips by shot_id and skips regenerating if the
    # file already exists, so without cleanup a later test would silently
    # reuse an earlier test's clip content.
    run_dir = REPO_ROOT / "shared" / "runs" / "test_run_11"
    if run_dir.exists():
        shutil.rmtree(run_dir)
    yield
    if run_dir.exists():
        shutil.rmtree(run_dir)
RENDER_CFG = {"output_width": 320, "output_height": 180, "fps": 25, "video_codec": "libx264", "video_crf": 28, "audio_codec": "aac", "audio_bitrate": "96k"}
THRESHOLDS = {"qa": {"duration_tolerance_pct": 2}}


def _make_color_clip(dest: Path, color: str, size: str, duration: float, fps: int = 25) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s={size}:d={duration}:r={fps}", "-pix_fmt", "yuv420p", str(dest)],
        capture_output=True, text=True, check=True,
    )


def _make_audio(dest: Path, duration: float) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}", str(dest)],
        capture_output=True, text=True, check=True,
    )


def _write_timeline(input_dir: Path, clips: list[dict]) -> Path:
    input_dir.mkdir(parents=True, exist_ok=True)
    timeline = {"run_id": "test_run_11", "scene_id": "s1", "clips": clips, "total_duration_s": clips[-1]["timeline_end_s"]}
    (input_dir / "timeline.json").write_text(json.dumps(timeline), encoding="utf-8")
    return input_dir


def test_complete_happy_path_with_crossfade(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    clip_a = tmp_path / "assets" / "a.mp4"
    clip_b = tmp_path / "assets" / "b.mp4"
    _make_color_clip(clip_a, "red", "320x240", 3.0)
    _make_color_clip(clip_b, "blue", "640x360", 2.0)
    audio_path = input_dir / "scene_mix.wav"

    clips = [
        {"shot_id": "s1", "file_ref": str(clip_a), "source_in_s": 0.0, "source_out_s": 3.0, "timeline_start_s": 0.0, "timeline_end_s": 3.0, "transition_out": {"type": "crossfade", "duration_s": 0.75}},
        {"shot_id": "s2", "file_ref": str(clip_b), "source_in_s": 0.0, "source_out_s": 2.0, "timeline_start_s": 3.0, "timeline_end_s": 5.0},
    ]
    _write_timeline(input_dir, clips)
    _make_audio(audio_path, 4.25)  # 3.0 + 2.0 - 0.75 crossfade overlap

    response = run.main(input_dir, output_dir, RUN_CONFIG, render_cfg=RENDER_CFG, thresholds=THRESHOLDS)

    assert response.status.value == "COMPLETE"
    final = output_dir / "final.mp4"
    assert final.exists()
    duration = run.probe_duration_s(final)
    assert abs(duration - 4.25) < 0.1


def test_hard_cut_and_dip_to_black(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    clip_a = tmp_path / "assets" / "a.mp4"
    clip_b = tmp_path / "assets" / "b.mp4"
    clip_c = tmp_path / "assets" / "c.mp4"
    _make_color_clip(clip_a, "red", "320x240", 2.0)
    _make_color_clip(clip_b, "green", "320x240", 2.0)
    _make_color_clip(clip_c, "blue", "320x240", 2.0)
    audio_path = input_dir / "scene_mix.wav"

    clips = [
        {"shot_id": "s1", "file_ref": str(clip_a), "source_in_s": 0.0, "source_out_s": 2.0, "timeline_start_s": 0.0, "timeline_end_s": 2.0, "transition_out": {"type": "hard-cut", "duration_s": 0.0}},
        {"shot_id": "s2", "file_ref": str(clip_b), "source_in_s": 0.0, "source_out_s": 2.0, "timeline_start_s": 2.0, "timeline_end_s": 4.0, "transition_out": {"type": "dip-to-black", "duration_s": 0.5}},
        {"shot_id": "s3", "file_ref": str(clip_c), "source_in_s": 0.0, "source_out_s": 2.0, "timeline_start_s": 4.0, "timeline_end_s": 6.0},
    ]
    _write_timeline(input_dir, clips)
    _make_audio(audio_path, 6.0)  # no crossfades - full sum, unaffected

    response = run.main(input_dir, output_dir, RUN_CONFIG, render_cfg=RENDER_CFG, thresholds=THRESHOLDS)

    assert response.status.value == "COMPLETE"
    duration = run.probe_duration_s(output_dir / "final.mp4")
    assert abs(duration - 6.0) < 0.1


def test_missing_input_files_fails(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    input_dir.mkdir()

    response = run.main(input_dir, output_dir, RUN_CONFIG, render_cfg=RENDER_CFG, thresholds=THRESHOLDS)

    assert response.status.value == "FAILED"


def test_empty_clips_fails(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "timeline.json").write_text(json.dumps({"run_id": "test_run_11", "scene_id": "s1", "clips": [], "total_duration_s": 0}), encoding="utf-8")
    _make_audio(input_dir / "scene_mix.wav", 1.0)

    response = run.main(input_dir, output_dir, RUN_CONFIG, render_cfg=RENDER_CFG, thresholds=THRESHOLDS)

    assert response.status.value == "FAILED"


def test_missing_clip_file_fails(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    clips = [
        {"shot_id": "s1", "file_ref": str(tmp_path / "does_not_exist.mp4"), "source_in_s": 0.0, "source_out_s": 2.0, "timeline_start_s": 0.0, "timeline_end_s": 2.0},
    ]
    _write_timeline(input_dir, clips)
    _make_audio(input_dir / "scene_mix.wav", 2.0)

    response = run.main(input_dir, output_dir, RUN_CONFIG, render_cfg=RENDER_CFG, thresholds=THRESHOLDS)

    assert response.status.value == "FAILED"
