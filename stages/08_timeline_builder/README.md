# 08_timeline_builder

**Type:** CODE — see `CLAUDE.md` §2 and `ARCHITECTURE.md` §2.

## Purpose

Materializes the approved `edit_plan.json` + `assets_manifest.json` into `timeline.json`: absolute timecodes, file references, transition parameters. Pure transformation + validation — zero creative decisions (those were already made and approved in 07).

## I/O

- Input: `inputs/edit_plan.json` (approved), `inputs/assets_manifest.json`, optionally `inputs/audio_mix.json` (from Stage 09 — see narration reconciliation below; absent is fine, behaves exactly as before).
- Output: `outputs/timeline.json` (schema: `shared/schemas/timeline.schema.json`), or a `FALLBACK_ROUTED` response (reason `asset_too_short_for_narration`) if a beat's asset can't be stretched to cover its narration even from `in_s=0`.

## Run / test instructions

Implemented — pure CODE, no agent, no external services:

```
python -m pytest stages/08_timeline_builder/tests/ -v

python stages/08_timeline_builder/src/run.py \
  stages/08_timeline_builder/inputs \
  stages/08_timeline_builder/outputs \
  <path-to-run_config.yaml>
```

Trims each shot to `[in_s, in_s + hold_duration_s]` — **not** `[in_s, out_s]` — per the clarified semantics in `edit_plan.schema.json` (`out_s` is an availability ceiling, `hold_duration_s` is authoritative). Defensively re-checks that `hold_duration_s` fits within `[in_s, out_s]` and within the asset's actual `duration_s`, even though Stage 07 should already guarantee both.

**Narration reconciliation (2026-07-15, see `ARCHITECTURE.md`).** When `inputs/audio_mix.json` is present, each beat's `hold_duration_s` is extended (never shortened; multi-shot beats scaled proportionally) to at least cover its `narration_stems` duration, since audio timing is authoritative for this narrated-prose format. A beat whose asset can't cover its narration even from `in_s=0` is routed `FALLBACK_ROUTED` rather than clipped/looped — resolve it the same way any fallback routing gets resolved: re-run Stage 06 for that beat with `beats.json`'s `est_duration_s` overridden to the required narration duration (plus a small safety margin — a razor-thin exact match risks a sub-frame rounding shortfall, which happened for real during development), patch the winning `assets_manifest.json` entry and the beat's `asset_id`/shot window in `edit_plan.json`, then re-run this stage.

## Numeric pass criterion

100% schema-valid; the last clip's `timeline_end_s` equals `total_duration_s` exactly; re-running with the same inputs produces an identical `timeline.json` (deterministic transform); with `audio_mix.json` present, every clip's `[timeline_start_s, timeline_end_s]` matches its beat's narration `[start_s, start_s+duration_s]` exactly.

**Result (2026-07-15, against Stage 07's real 5-beat edit plan + Stage 09's real audio_mix.json): PASS.** Beat b002's real retrieved asset (6.0s) couldn't cover its narration (12.84s) — routed `FALLBACK_ROUTED` as designed, resolved by re-running Stage 06 for that one beat with a corrected target duration (a real generated close-up of the trunk/brass-latches, matching the beat well). Final `timeline.json`: 5 clips, `total_duration_s=63.1118` — verified to match `audio_mix.json`'s `total_duration_s` exactly, and every clip's timeline position matches its beat's narration timing to within 0.1ms. 10/10 unit tests pass (original 6 plus 4 new: hold extended when the asset covers it, hold left alone when already sufficient, `FALLBACK_ROUTED` when the asset can't cover it, proportional scaling across a multi-shot beat).

## Review checklist

- [x] No field in `timeline.json` was not already present in `edit_plan.json`/`assets_manifest.json` — nothing invented here.
- [x] Deterministic — re-running with the same inputs produces byte-identical output (no randomness anywhere in this stage).
- [x] Narration reconciliation only extends holds, never shortens or drops content.
- [x] A too-short asset routes to fallback rather than silently clipping/looping (Rule 7).
