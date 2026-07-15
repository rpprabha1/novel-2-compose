# 08_timeline_builder

**Type:** CODE — see `CLAUDE.md` §2 and `ARCHITECTURE.md` §2.

## Purpose

Materializes the approved `edit_plan.json` + `assets_manifest.json` into `timeline.json`: absolute timecodes, file references, transition parameters. Pure transformation + validation — zero creative decisions (those were already made and approved in 07).

## I/O

- Input: `inputs/edit_plan.json` (approved), `inputs/assets_manifest.json`.
- Output: `outputs/timeline.json` (schema: `shared/schemas/timeline.schema.json`).

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

## Numeric pass criterion

100% schema-valid; the last clip's `timeline_end_s` equals `total_duration_s` exactly; re-running with the same inputs produces an identical `timeline.json` (deterministic transform).

**Result (2026-07-14, against Stage 07's real 5-beat edit plan): PASS, first try.** `timeline.json` has 5 sequential clips (0.0s→15.5s), transitions carried through correctly (`crossfade` after b002, `dip-to-black` after b004, `hard-cut` elsewhere, no `transition_out` on the final clip), every `file_ref` points to a real cached video from Stage 05, `source_out_s` correctly reflects `hold_duration_s` rather than the looser `out_s`. 6/6 unit tests pass (sequential offsets, intra-beat multi-shot hard-cut, the in_s/out_s/hold_duration_s trim semantics explicitly, missing-asset and hold-exceeds-duration `FAILED` guards, missing-input `FAILED`).

## Review checklist

- [x] No field in `timeline.json` was not already present in `edit_plan.json`/`assets_manifest.json` — nothing invented here.
- [x] Deterministic — re-running with the same inputs produces byte-identical output (no randomness anywhere in this stage).

## Known follow-up (2026-07-15)

The `timeline.json` produced above (`total_duration_s=15.5`) is now **stale**. Stage 09 discovered that narration (reading each beat's full source paragraph aloud) takes far longer than the visual `hold_duration_s` this stage trimmed to (see `stages/09_audio_production/README.md`'s architecture-change note and `ARCHITECTURE.md` 2026-07-15). Before `11_assembly_render` can use this timeline, each beat's shot needs to be regenerated with its hold stretched to at least match its narration duration from `audio_mix.json`'s `narration_stems`. This stage's own logic doesn't need to change (still a pure transform) — it needs to be re-run against an `edit_plan.json` whose `hold_duration_s` values have already been reconciled with narration length upstream. That reconciliation step doesn't exist yet.
