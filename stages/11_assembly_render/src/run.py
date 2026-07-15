"""Stage 11: assembly_render.

Deterministic ffmpeg pipeline: normalizes mixed-source clips to one canvas,
concatenates with transitions (hard-cut/match-cut-suggestion: instant;
crossfade: borrows time from each side via xfade; dip-to-black: in-place
fade, no runtime change), reconciles the resulting video duration against
the audio-driven timeline (crossfades shorten video slightly - see README),
and muxes the final audio track. CODE, fully deterministic - no decisions
made here that weren't already fixed upstream (CLAUDE.md).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from shared.envelopes import ErrorInfo, StageResponse, StageStatus  # noqa: E402
from shared.media import (  # noqa: E402
    FFmpegError,
    concat_hard_cut,
    dip_to_black_transition,
    match_duration,
    mux_video_audio,
    normalize_clip,
    probe_duration_s,
    xfade_transition,
)

STAGE_NAME = "11_assembly_render"


def main(input_dir: Path, output_dir: Path, run_config: dict, render_cfg: dict | None = None, thresholds: dict | None = None) -> StageResponse:
    run_id = run_config["run_id"]
    timeline_path = input_dir / "timeline.json"
    audio_path = input_dir / "scene_mix.wav"

    missing = [p.name for p in (timeline_path, audio_path) if not p.exists()]
    if missing:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(message=f"Missing required input file(s) in {input_dir}: {missing}"),
        )

    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    clips = timeline.get("clips", [])
    if not clips:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(message="timeline.json has no clips - nothing to assemble."),
        )

    render_cfg = render_cfg or yaml.safe_load((REPO_ROOT / "config" / "render.yaml").read_text(encoding="utf-8"))
    thresholds = thresholds or yaml.safe_load((REPO_ROOT / "config" / "thresholds.yaml").read_text(encoding="utf-8"))
    width, height, fps = render_cfg["output_width"], render_cfg["output_height"], render_cfg["fps"]
    video_codec, crf = render_cfg["video_codec"], render_cfg["video_crf"]
    audio_codec, audio_bitrate = render_cfg["audio_codec"], render_cfg["audio_bitrate"]
    duration_tolerance_pct = thresholds["qa"]["duration_tolerance_pct"]

    cache_dir = REPO_ROOT / "shared" / "runs" / run_id / "cache" / "assembly"

    normalized: list[Path] = []
    for clip in clips:
        src = REPO_ROOT / clip["file_ref"]
        dest = cache_dir / f"norm_{clip['shot_id']}.mp4"
        try:
            if not dest.exists():
                normalize_clip(src, clip["source_in_s"], clip["source_out_s"], width, height, fps, video_codec, crf, dest)
        except FFmpegError as exc:
            return StageResponse(
                envelope_id="",
                run_id=run_id,
                stage=STAGE_NAME,
                status=StageStatus.FAILED,
                error=ErrorInfo(message=f"Clip normalization failed for {clip['shot_id']!r}", diagnostics=str(exc)),
            )
        normalized.append(dest)

    current = normalized[0]
    try:
        running_duration = probe_duration_s(current)
        for i in range(1, len(clips)):
            transition = clips[i - 1].get("transition_out") or {"type": "hard-cut", "duration_s": 0.0}
            b = normalized[i]
            combined = cache_dir / f"combined_{i:02d}.mp4"
            ttype, tdur = transition.get("type", "hard-cut"), transition.get("duration_s", 0.0)
            if ttype == "crossfade" and tdur > 0:
                xfade_transition(current, b, running_duration, tdur, video_codec, crf, combined)
                running_duration = running_duration + probe_duration_s(b) - tdur
            elif ttype == "dip-to-black" and tdur > 0:
                dip_to_black_transition(current, b, tdur, video_codec, crf, combined)
                running_duration += probe_duration_s(b)
            else:
                concat_hard_cut(current, b, video_codec, crf, combined)
                running_duration += probe_duration_s(b)
            current = combined
    except FFmpegError as exc:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(message="Clip concatenation/transition failed", diagnostics=str(exc)),
        )

    # Audio timing is authoritative (see 09_audio_production) - reconcile the
    # assembled video (shortened slightly by any crossfades) to match it
    # exactly rather than leave mismatched stream lengths for a player to
    # resolve however it likes.
    audio_duration = probe_duration_s(audio_path)
    matched_video = cache_dir / "matched.mp4"
    try:
        match_duration(current, audio_duration, matched_video)
    except FFmpegError as exc:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(message="Duration reconciliation against audio failed", diagnostics=str(exc)),
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = output_dir / "final.mp4"
    try:
        mux_video_audio(matched_video, audio_path, audio_codec, audio_bitrate, final_path)
    except FFmpegError as exc:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(message="Final mux failed", diagnostics=str(exc)),
        )

    final_duration = probe_duration_s(final_path)
    drift_pct = abs(final_duration - audio_duration) / audio_duration * 100 if audio_duration else 0.0

    if drift_pct > duration_tolerance_pct:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(
                message=f"final.mp4 duration {final_duration:.3f}s drifts {drift_pct:.2f}% from the audio target "
                f"{audio_duration:.3f}s (limit {duration_tolerance_pct}%) despite explicit reconciliation - a real bug."
            ),
        )

    return StageResponse(
        envelope_id="",
        run_id=run_id,
        stage=STAGE_NAME,
        status=StageStatus.COMPLETE,
        summary=(
            f"Assembled final.mp4: {len(clips)} clip(s), duration={final_duration:.3f}s "
            f"(audio target {audio_duration:.3f}s, drift {drift_pct:.3f}%)."
        ),
        output_manifest=["outputs/final.mp4"],
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
