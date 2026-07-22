import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

# See stage 01's test_run.py for why this isn't a plain "import run".
STAGE_SRC = Path(__file__).resolve().parents[1] / "src"
_spec = importlib.util.spec_from_file_location("stage14_anime_style_conversion_run", STAGE_SRC / "run.py")
run = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = run
_spec.loader.exec_module(run)

RUN_CONFIG = {"run_id": "test_run_14"}
ANIME_STYLE_SPEC = {"checkpoint": "paprika", "device": "cpu", "target_width": 64, "stylize_fps": 2, "output_fps": 10}
RENDER_CFG = {"video_codec": "libx264", "video_crf": 28, "audio_codec": "aac", "audio_bitrate": "96k"}
THRESHOLDS = {"anime_style": {"duration_tolerance_pct": 2}}


def _make_final_mp4(dest: Path, duration: float = 2.0, size: str = "64x48") -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=blue:s={size}:d={duration}:r=10",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            "-shortest", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(dest),
        ],
        capture_output=True, text=True, check=True,
    )


def _fake_stylizer(src_path, dest_path, **kwargs):
    """Mocked model call (CLAUDE.md fixture rule: never a live model call in
    tests) - a real AnimeGANv2 pass would re-encode but not re-time the
    video, so copying the source file is a faithful-enough stand-in for
    testing this stage's own file-handling and duration-check logic."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src_path, dest_path)


def test_complete_happy_path(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    _make_final_mp4(input_dir / "final.mp4", duration=2.0, size="64x48")

    response = run.main(
        input_dir, output_dir, RUN_CONFIG,
        anime_style_spec=ANIME_STYLE_SPEC, render_cfg=RENDER_CFG, thresholds=THRESHOLDS,
        stylizer=_fake_stylizer,
    )

    assert response.status.value == "COMPLETE"
    dest = output_dir / "final_anime.mp4"
    assert dest.exists()
    assert response.output_manifest == ["outputs/final_anime.mp4"]
    out_duration = run.probe_duration_s(dest)
    assert abs(out_duration - 2.0) / 2.0 * 100 <= THRESHOLDS["anime_style"]["duration_tolerance_pct"]


def test_missing_final_mp4_fails(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    input_dir.mkdir()

    response = run.main(
        input_dir, output_dir, RUN_CONFIG,
        anime_style_spec=ANIME_STYLE_SPEC, render_cfg=RENDER_CFG, thresholds=THRESHOLDS,
        stylizer=_fake_stylizer,
    )

    assert response.status.value == "FAILED"
    assert not (output_dir / "final_anime.mp4").exists()


def test_duration_drift_beyond_tolerance_fails(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    _make_final_mp4(input_dir / "final.mp4", duration=2.0, size="64x48")

    def _drifting_stylizer(src_path, dest_path, **kwargs):
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "color=c=blue:s=64x48:d=3.0:r=10",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=3.0",
                "-shortest", "-pix_fmt", "yuv420p", "-c:a", "aac",
                str(dest_path),
            ],
            capture_output=True, text=True, check=True,
        )

    response = run.main(
        input_dir, output_dir, RUN_CONFIG,
        anime_style_spec=ANIME_STYLE_SPEC, render_cfg=RENDER_CFG, thresholds=THRESHOLDS,
        stylizer=_drifting_stylizer,
    )

    assert response.status.value == "FAILED"
