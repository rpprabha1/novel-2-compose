# 11_assembly_render

**Type:** CODE — see `CLAUDE.md` §2 and `ARCHITECTURE.md` §2.

## Purpose

Deterministic ffmpeg pipeline: trim, concat, color grade (if specified), mux audio, output `final.mp4` from the approved `timeline.json` + `audio_mix.json`. No decisions made here that weren't already fixed upstream.

## I/O

- Input: `inputs/timeline.json` (approved via 10), `inputs/audio_mix.json`.
- Output: `outputs/final.mp4`.

## Run / test instructions

Not yet implemented. Blocked on Gate 0. Tests render synthetic solid-color/silence fixtures only.

## Numeric pass criterion

`final.mp4` duration matches `timeline.total_duration_s` within `config/thresholds.yaml`'s `qa.duration_tolerance_pct`; 0 ffmpeg errors/warnings on a clean run.

## Review checklist

- [ ] Re-running with identical inputs produces duration-identical output (deterministic).
- [ ] No agent logic anywhere in this stage.
