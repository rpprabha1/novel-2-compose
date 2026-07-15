# 12_qa_attribution

**Type:** CODE — see `CLAUDE.md` §2 and `ARCHITECTURE.md` §2.

## Purpose

Validates every run artifact against its schema, checks every fetched asset in the run manifest has a complete license/attribution record (CC-BY assets without a creator record fail QA), checks final duration and loudness against configured tolerances. Emits `CREDITS.md` and a QA report.

## I/O

- Input: all run artifacts + `shared/runs/<run_id>/manifest.json`, `inputs/final.mp4`.
- Output: `outputs/qa_report.json` (schema: `shared/schemas/qa_report.schema.json`), `outputs/CREDITS.md`.

## Run / test instructions

Not yet implemented. Blocked on Gate 0.

## Numeric pass criterion

`qa_report.pass == true` requires 100% of checks (`schema_validation`, `attribution_completeness`, `duration_tolerance`, `loudness_spec`) passing. A failing report blocks the run from being marked done but does not block re-running upstream stages.

## Review checklist

- [ ] `CREDITS.md` lists every asset requiring attribution, with creator + source.
- [ ] Duration/loudness checks read tolerances from `config/thresholds.yaml` / `config/audio_spec.yaml`, never hardcode them.
