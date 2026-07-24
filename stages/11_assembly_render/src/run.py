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

import hashlib
import json
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from shared.envelopes import ErrorInfo, StageResponse, StageStatus  # noqa: E402
from shared.media import (  # noqa: E402
    FFmpegError,
    concat_stream_copy,
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
        # Cache key includes the clip's actual source parameters, not just
        # shot_id - a real bug (caught 2026-07-18): shot_id alone let a
        # re-run with a changed edit_plan/timeline.json (same run_id, same
        # shot_ids, different file_ref/source_in_s/source_out_s) silently
        # reuse stale normalized clips from an earlier render instead of
        # regenerating them, producing a final.mp4 that mixed correct and
        # leftover-wrong content/durations with no error or warning.
        cache_key = hashlib.sha256(
            f"{clip['file_ref']}|{clip['source_in_s']}|{clip['source_out_s']}".encode()
        ).hexdigest()[:16]
        dest = cache_dir / f"norm_{clip['shot_id']}_{cache_key}.mp4"
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

    # Build the render unit list: walk every clip boundary in order, exactly
    # like the original design (so every clip's own transition_out is honored,
    # including the rare case of two real transitions in a row - e.g. a
    # single-shot beat that both fades in and immediately fades out again).
    # A hard-cut/match-cut-suggestion boundary (duration 0) closes off the
    # current unit as-is; a real transition (crossfade/dip-to-black) blends
    # the current unit's tail against the next clip in place, so `current`
    # only ever grows across a genuine RUN of consecutive real transitions
    # (rare and short - almost always just 2 clips), never across the whole
    # video. Finished units are joined with one lossless stream-copy pass.
    #
    # Rewritten 2026-07-24 (real bug, see ARCHITECTURE.md change log): the
    # previous implementation instead treated `current` as the WHOLE growing
    # video and re-encoded it in full on every single clip boundary - O(n^2)
    # in clip count. Fine at the small scale this was originally tested at,
    # but 07_2_narration_shot_mapping's real output (hundreds of extracted
    # shots per scene) made this genuinely infeasible: a real run was still
    # short of finishing after 1.5+ hours with per-step time still climbing
    # (16s -> 187s and rising), on track for many more hours. Fixed to bound
    # re-encoding to only the actual beat-boundary transitions, with every
    # hard-cut boundary (the overwhelming majority - all of 07_2's intra-beat
    # shot cuts) joined for free via ffmpeg's concat demuxer (stream copy, no
    # decode/re-encode at all) - same transition semantics (crossfade still
    # borrows tdur from each side, dip-to-black still leaves total duration
    # unchanged), only the re-encoding COST changed.
    #
    # Bridge cache filenames are content-derived (hash of the two INPUT
    # filenames, which are themselves content-derived - normalize_clip's own
    # output names already hash file_ref/source_in_s/source_out_s), not
    # position-based - the same fix already applied to normalize_clip's own
    # cache (2026-07-18) for the identical staleness failure mode: a stale
    # bridge silently reused across a changed edit_plan/timeline.json.
    units: list[Path] = []
    current = normalized[0]
    try:
        for i in range(len(normalized) - 1):
            transition = clips[i].get("transition_out") or {"type": "hard-cut", "duration_s": 0.0}
            ttype, tdur = transition.get("type", "hard-cut"), transition.get("duration_s", 0.0)
            nxt = normalized[i + 1]
            if ttype in ("crossfade", "dip-to-black") and tdur > 0:
                bridge_key = hashlib.sha256(f"{current.name}|{nxt.name}|{ttype}|{tdur}".encode()).hexdigest()[:16]
                bridge = cache_dir / f"bridge_{bridge_key}.mp4"
                if not bridge.exists():
                    if ttype == "crossfade":
                        xfade_transition(current, nxt, probe_duration_s(current), tdur, video_codec, crf, bridge)
                    else:
                        dip_to_black_transition(current, nxt, tdur, video_codec, crf, bridge)
                current = bridge
            else:
                units.append(current)
                current = nxt
        units.append(current)

        assembled = cache_dir / "assembled.mp4"
        concat_stream_copy(units, assembled)
        current = assembled
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
