import importlib.util
import subprocess
import sys
from pathlib import Path

# See stage 01's test_run.py for why this isn't a plain "import run".
STAGE_SRC = Path(__file__).resolve().parents[1] / "src"
_spec = importlib.util.spec_from_file_location("stage13_pixel_art_conversion_run", STAGE_SRC / "run.py")
run = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = run
_spec.loader.exec_module(run)

RUN_CONFIG = {"run_id": "test_run_13"}
PIXEL_ART_SPEC = {
    "downscale_factor": 8,
    "max_colors": 16,
    "palettegen_stats_mode": "diff",
    "dither_method": "bayer",
    "bayer_scale": 3,
    "edge_low": 0.1,
    "edge_high": 0.35,
}
RENDER_CFG = {"video_codec": "libx264", "video_crf": 28, "audio_codec": "aac", "audio_bitrate": "96k"}
THRESHOLDS = {"pixel_art": {"duration_tolerance_pct": 2}}


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


def test_complete_happy_path(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    _make_final_mp4(input_dir / "final.mp4", duration=2.0, size="64x48")

    response = run.main(input_dir, output_dir, RUN_CONFIG, pixel_art_spec=PIXEL_ART_SPEC, render_cfg=RENDER_CFG, thresholds=THRESHOLDS)

    assert response.status.value == "COMPLETE"
    dest = output_dir / "final_pixel_art.mp4"
    assert dest.exists()
    assert response.output_manifest == ["outputs/final_pixel_art.mp4"]
    out_width, out_height = run.probe_resolution(dest)
    assert (out_width, out_height) == (64, 48)
    out_duration = run.probe_duration_s(dest)
    assert abs(out_duration - 2.0) / 2.0 * 100 <= THRESHOLDS["pixel_art"]["duration_tolerance_pct"]


def test_missing_final_mp4_fails(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    input_dir.mkdir()

    response = run.main(input_dir, output_dir, RUN_CONFIG, pixel_art_spec=PIXEL_ART_SPEC, render_cfg=RENDER_CFG, thresholds=THRESHOLDS)

    assert response.status.value == "FAILED"
    assert not (output_dir / "final_pixel_art.mp4").exists()


def test_pixel_grid_scales_with_resolution_and_rounds_even():
    assert run._compute_pixel_grid(1920, 1080, 8) == (240, 136)
    assert run._compute_pixel_grid(64, 48, 8) == (8, 6)
    # Tiny inputs never collapse to a zero-size or odd grid.
    assert run._compute_pixel_grid(10, 10, 8) == (2, 2)
