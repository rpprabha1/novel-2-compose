"""Stage 13: pixel_art_conversion.

Deterministic ffmpeg pipeline that restyles the QA-approved final.mp4 into a
retro pixel-art look: nearest-neighbor downscale to a pixel grid, palette
reduction with ordered (bayer) dithering, nearest-neighbor upscale back to
the source resolution. CODE, fully deterministic - the creative choice
(which of 3 sampled ffmpeg techniques looks best) was already made by the
human reviewing real samples rendered from the real final.mp4 (see
ARCHITECTURE.md change log, 2026-07-18); nothing here involves judgment
(CLAUDE.md).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from shared.envelopes import ErrorInfo, StageResponse, StageStatus  # noqa: E402
from shared.media import FFmpegError, apply_pixel_art_style, probe_duration_s, probe_resolution  # noqa: E402

STAGE_NAME = "13_pixel_art_conversion"


def _compute_pixel_grid(width: int, height: int, downscale_factor: int) -> tuple[int, int]:
    grid_w = max(2, round(width / downscale_factor / 2) * 2)
    grid_h = max(2, round(height / downscale_factor / 2) * 2)
    return grid_w, grid_h


def main(
    input_dir: Path,
    output_dir: Path,
    run_config: dict,
    pixel_art_spec: dict | None = None,
    render_cfg: dict | None = None,
    thresholds: dict | None = None,
) -> StageResponse:
    run_id = run_config["run_id"]
    src_path = input_dir / "final.mp4"
    if not src_path.exists():
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(message=f"final.mp4 not found in {input_dir} - nothing to convert."),
        )

    pixel_art_spec = pixel_art_spec or yaml.safe_load((REPO_ROOT / "config" / "pixel_art_spec.yaml").read_text(encoding="utf-8"))
    render_cfg = render_cfg or yaml.safe_load((REPO_ROOT / "config" / "render.yaml").read_text(encoding="utf-8"))
    thresholds = thresholds or yaml.safe_load((REPO_ROOT / "config" / "thresholds.yaml").read_text(encoding="utf-8"))

    try:
        src_width, src_height = probe_resolution(src_path)
        src_duration = probe_duration_s(src_path)
    except FFmpegError as exc:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(message=f"Could not probe {src_path}", diagnostics=str(exc)),
        )

    grid_w, grid_h = _compute_pixel_grid(src_width, src_height, pixel_art_spec["downscale_factor"])

    output_dir.mkdir(parents=True, exist_ok=True)
    dest_path = output_dir / "final_pixel_art.mp4"

    try:
        apply_pixel_art_style(
            src_path,
            grid_w,
            grid_h,
            pixel_art_spec["max_colors"],
            pixel_art_spec["palettegen_stats_mode"],
            pixel_art_spec["dither_method"],
            pixel_art_spec["bayer_scale"],
            pixel_art_spec["edge_low"],
            pixel_art_spec["edge_high"],
            src_width,
            src_height,
            render_cfg["video_codec"],
            render_cfg["video_crf"],
            render_cfg["audio_codec"],
            render_cfg["audio_bitrate"],
            dest_path,
        )
    except FFmpegError as exc:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(message="Pixel-art conversion failed", diagnostics=str(exc)),
        )

    out_duration = probe_duration_s(dest_path)
    drift_pct = abs(out_duration - src_duration) / src_duration * 100 if src_duration else 0.0
    duration_tolerance_pct = thresholds["pixel_art"]["duration_tolerance_pct"]

    if drift_pct > duration_tolerance_pct:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(
                message=f"final_pixel_art.mp4 duration {out_duration:.3f}s drifts {drift_pct:.3f}% from "
                f"source {src_duration:.3f}s (limit {duration_tolerance_pct}%) - a real bug, this stage "
                f"only re-filters frames and should not change timing."
            ),
        )

    return StageResponse(
        envelope_id="",
        run_id=run_id,
        stage=STAGE_NAME,
        status=StageStatus.COMPLETE,
        summary=(
            f"Converted final.mp4 to pixel art: grid={grid_w}x{grid_h}, {pixel_art_spec['max_colors']}-color "
            f"{pixel_art_spec['dither_method']}-dithered palette, edge-detected outline overlay "
            f"(low={pixel_art_spec['edge_low']}, high={pixel_art_spec['edge_high']}), "
            f"duration={out_duration:.3f}s (source {src_duration:.3f}s, drift {drift_pct:.3f}%)."
        ),
        output_manifest=["outputs/final_pixel_art.mp4"],
    )


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python run.py <input_dir> <output_dir> <run_config.yaml>")
        sys.exit(1)
    in_dir, out_dir, config_path = (Path(a) for a in sys.argv[1:4])
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    result = main(in_dir, out_dir, cfg)
    print(json.dumps(result.to_dict(), indent=2))
    sys.exit(0 if result.status == StageStatus.COMPLETE else 1)
