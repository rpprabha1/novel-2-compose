# 11_assembly_render

**Type:** CODE — see `CLAUDE.md` §2 and `ARCHITECTURE.md` §2.

## Purpose

Deterministic ffmpeg pipeline: normalize mixed-source clips to one canvas, trim, concat with transitions, mux audio, output `final.mp4` from the approved `timeline.json` + Stage 09's rendered `scene_mix.wav`. No decisions made here that weren't already fixed upstream.

## I/O

- Input: `inputs/timeline.json` (approved via 10), `inputs/scene_mix.wav` (09's final rendered mix — not `audio_mix.json`; the stems/params in that file exist for the record and for Stage 10's contact sheet, but the actual audio comes from the file Stage 09 already rendered).
- Output: `outputs/final.mp4`.

## Run / test instructions

Implemented, using `shared/media/assembly.py` (normalize, transitions, duration reconciliation, mux):

```
python -m pytest stages/11_assembly_render/tests/ -v   # real ffmpeg against tiny synthetic color/sine clips

python stages/11_assembly_render/src/run.py \
  stages/11_assembly_render/inputs \
  stages/11_assembly_render/outputs \
  <path-to-run_config.yaml>
```

**Why crossfade needs special handling.** Source clips come from mixed resolutions/aspect ratios (portrait stock footage, landscape stock footage, a square generated fallback) and are normalized to one canvas (`config/render.yaml`, default 1920x1080) by scaling to fit and letterboxing/pillarboxing — never cropping or distorting. Transitions are applied while chaining clips together: `hard-cut`/`match-cut-suggestion` are instant (simple concat); `crossfade` uses ffmpeg's `xfade`, which **borrows** its duration from each side (the combined output is `a_duration + b_duration - transition_duration`, exactly how editors budget a real crossfade) rather than adding extra runtime; `dip-to-black` is an in-place fade on each clip's own existing time and doesn't change total duration at all. Because audio timing is authoritative (Stage 09) and crossfades shorten the video slightly, the assembled video is explicitly reconciled to the audio's exact duration (`match_duration` — pads via freeze-frame or trims) before the final mux, rather than leaving mismatched stream lengths for a player to resolve however it likes. `config/editorial_vocab.yaml`'s `transition_durations_s` (added 2026-07-15, see `ARCHITECTURE.md`) is what actually gives crossfade/dip-to-black a nonzero duration — Stage 08 previously hardcoded every transition to `0.0`, which would have made a "crossfade" indistinguishable from a hard-cut.

## Numeric pass criterion

`final.mp4` duration matches the audio target within `config/thresholds.yaml`'s `qa.duration_tolerance_pct`; 0 ffmpeg errors/warnings on a clean run.

**Result (2026-07-15, against the real scene's timeline.json + scene_mix.wav): PASS.** `final.mp4`: 1920x1080 h264 + aac, 63.113s, **0.000% drift** from the audio target (63.113s) — the explicit duration reconciliation worked exactly as designed. Spot-checked the crossfade transition by extracting frames at 13.9s (clearly still the b001 staircase) and 14.55s (clearly the b002 trunk/latch close-up, correctly pillarboxed from the square generated source) — confirmed the blend is real, not a silent pass-through. 5/5 unit tests pass (crossfade duration math, hard-cut + dip-to-black chaining, missing-input/empty-clips/missing-clip-file `FAILED` paths) — including a proactive fix for the same cross-test cache-isolation bug found twice already this session (shot_ids like `s1`/`s2` repeat across test functions with different content).

## Review checklist

- [x] Re-running with identical inputs produces duration-identical output (deterministic) — cached intermediates keyed by shot_id/step.
- [x] No agent logic anywhere in this stage.
- [x] Mixed source resolutions/aspect ratios are letterboxed, never cropped or distorted.
- [x] Video duration is explicitly reconciled to the audio-driven target before muxing, not left to chance.
