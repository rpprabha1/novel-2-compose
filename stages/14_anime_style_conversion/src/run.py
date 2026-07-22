"""Stage 14: anime_style_conversion.

Restyles the QA-approved final.mp4 into an anime look via AnimeGANv2 (a
pretrained GAN, MIT-licensed, vendored under shared/models/animegan/). CODE,
fully deterministic - no agent involvement and no creative judgment left to
make here: the human picked the checkpoint (paprika) after reviewing 4 real
rendered samples, and separately decided to apply it uniformly (including
text-card beats, where it's known to blur legibility) rather than mask it to
real-footage clips only - see ARCHITECTURE.md change log. This stage only
mechanizes those already-made choices, the same pattern Stage 13 established
for pixel-art. Produces final_anime.mp4 alongside (never replacing) final.mp4
and final_pixel_art.mp4.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Callable

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from shared.envelopes import ErrorInfo, StageResponse, StageStatus  # noqa: E402
from shared.generation import AnimeGANError, stylize_video  # noqa: E402
from shared.media import FFmpegError, probe_duration_s  # noqa: E402

STAGE_NAME = "14_anime_style_conversion"

StylizerFn = Callable[..., None]


def main(
    input_dir: Path,
    output_dir: Path,
    run_config: dict,
    anime_style_spec: dict | None = None,
    render_cfg: dict | None = None,
    thresholds: dict | None = None,
    stylizer: StylizerFn | None = None,
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

    anime_style_spec = anime_style_spec or yaml.safe_load((REPO_ROOT / "config" / "anime_style_spec.yaml").read_text(encoding="utf-8"))
    render_cfg = render_cfg or yaml.safe_load((REPO_ROOT / "config" / "render.yaml").read_text(encoding="utf-8"))
    thresholds = thresholds or yaml.safe_load((REPO_ROOT / "config" / "thresholds.yaml").read_text(encoding="utf-8"))

    try:
        src_duration = probe_duration_s(src_path)
    except FFmpegError as exc:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(message=f"Could not probe {src_path}", diagnostics=str(exc)),
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    dest_path = output_dir / "final_anime.mp4"
    checkpoint_path = REPO_ROOT / "shared" / "models" / "animegan" / f"{anime_style_spec['checkpoint']}.pt"
    stylizer = stylizer or stylize_video

    upscale_cfg = anime_style_spec.get("upscale") or {}
    upscale_binary = None
    if upscale_cfg.get("enabled"):
        candidate = REPO_ROOT / upscale_cfg["binary_path"]
        if candidate.exists():
            upscale_binary = candidate

    with tempfile.TemporaryDirectory(prefix="anime_style_") as tmp:
        try:
            stylizer(
                src_path=src_path,
                dest_path=dest_path,
                checkpoint_path=checkpoint_path,
                device=anime_style_spec["device"],
                target_width=anime_style_spec["target_width"],
                stylize_fps=anime_style_spec["stylize_fps"],
                output_fps=anime_style_spec["output_fps"],
                video_codec=render_cfg["video_codec"],
                video_crf=render_cfg["video_crf"],
                audio_codec=render_cfg["audio_codec"],
                audio_bitrate=render_cfg["audio_bitrate"],
                work_dir=Path(tmp) / "frames",
                upscale_binary=upscale_binary,
                upscale_model=upscale_cfg.get("model", "realesr-animevideov3"),
                upscale_factor=upscale_cfg.get("scale", 2),
            )
        except AnimeGANError as exc:
            return StageResponse(
                envelope_id="",
                run_id=run_id,
                stage=STAGE_NAME,
                status=StageStatus.FAILED,
                error=ErrorInfo(message="Anime-style conversion failed", diagnostics=str(exc)),
            )

    out_duration = probe_duration_s(dest_path)
    drift_pct = abs(out_duration - src_duration) / src_duration * 100 if src_duration else 0.0
    duration_tolerance_pct = thresholds["anime_style"]["duration_tolerance_pct"]

    if drift_pct > duration_tolerance_pct:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(
                message=f"final_anime.mp4 duration {out_duration:.3f}s drifts {drift_pct:.3f}% from "
                f"source {src_duration:.3f}s (limit {duration_tolerance_pct}%)."
            ),
        )

    return StageResponse(
        envelope_id="",
        run_id=run_id,
        stage=STAGE_NAME,
        status=StageStatus.COMPLETE,
        summary=(
            f"Converted final.mp4 to anime style: checkpoint={anime_style_spec['checkpoint']}, "
            f"width={anime_style_spec['target_width']}, stylize_fps={anime_style_spec['stylize_fps']}, "
            f"output_fps={anime_style_spec['output_fps']}, duration={out_duration:.3f}s "
            f"(source {src_duration:.3f}s, drift {drift_pct:.3f}%)."
        ),
        output_manifest=["outputs/final_anime.mp4"],
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
