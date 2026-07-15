# 12_qa_attribution

**Type:** CODE — see `CLAUDE.md` §2 and `ARCHITECTURE.md` §2.

## Purpose

Validates every run artifact against its schema, checks every fetched asset in the run manifest has a complete license/attribution record (CC-BY assets without a creator record fail QA), checks final duration and loudness against configured tolerances. Emits `CREDITS.md` and a QA report.

## I/O

- Input: every JSON artifact from Stages 02/04/05(or 06)/07/08/09 (`beats.json`, `candidates.json`, `assets_manifest.json`, `edit_plan.json`, `timeline.json`, `music_cue_intent.json`, `audio_mix.json`), `shared/runs/<run_id>/manifest.json`, `inputs/final.mp4`.
- Output: `outputs/qa_report.json` (schema: `shared/schemas/qa_report.schema.json`), `outputs/CREDITS.md`.

## Run / test instructions

Implemented — pure CODE, no agent, no external services beyond ffprobe for the duration check:

```
python -m pytest stages/12_qa_attribution/tests/ -v

python stages/12_qa_attribution/src/run.py \
  stages/12_qa_attribution/inputs \
  stages/12_qa_attribution/outputs \
  <path-to-run_config.yaml>
```

Every artifact is required (missing = a `schema_validation` failure, not silently skipped). `final.mp4` missing entirely is a hard `FAILED` with no report written (nothing to QA); any of the four checks failing still writes `qa_report.json`/`CREDITS.md` (so the human can see exactly what's wrong) but returns `FAILED` overall.

## Numeric pass criterion

`qa_report.pass == true` requires 100% of checks (`schema_validation`, `attribution_completeness`, `duration_tolerance`, `loudness_spec`) passing. A failing report blocks the run from being marked done but does not block re-running upstream stages.

**Result (2026-07-15, against every real artifact from this run): PASS, after fixing two real manifest bugs.** All 4 checks green: 8/8 artifacts schema-valid, all 28 manifest entries have complete attribution, `final.mp4`=63.113s vs target=63.112s (0.002% drift), `final_lufs`=-15.97 vs target -16.0 (0.03 diff). Along the way, real `CREDITS.md` output revealed `shared/manifest.py` had been silently accumulating true duplicates (the same asset listed 2-3 times) because several stages were re-run during this session's debugging — fixed `append_manifest_entries()` to de-duplicate against what's already on disk, not just within one call. That fix then exposed a second bug: Stage 05's manifest entries never carried `source_url` (only Stage 03's did), so de-duplication was clobbering the more complete entry — fixed Stage 05 to carry the candidate's URL through. 6/6 unit tests pass (all-pass, missing-artifact, missing-attribution, duration-drift, loudness-drift, missing-final.mp4), plus 3 new regression tests directly on `shared/manifest.py` for the de-dup fix.

## Review checklist

- [x] `CREDITS.md` lists every asset requiring attribution, with creator + source — verified no duplicates and all 5 real winning footage assets have working source URLs.
- [x] Duration/loudness checks read tolerances from `config/thresholds.yaml` / `config/audio_spec.yaml`, never hardcode them.
