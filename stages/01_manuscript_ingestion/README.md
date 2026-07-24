# 01_manuscript_ingestion

**Type:** CODE — see `CLAUDE.md` §2 and `ARCHITECTURE.md` §2.

## Purpose

Story-text intake for the director-driven flow (step 1). Splits a raw manuscript text file into per-scene text files using the explicit marker convention declared in `run_config.yaml` (`scene_marker_convention.chapter_marker` / `scene_marker`), and normalizes encoding to UTF-8. No interpretation of content — pure text splitting, no judgment.

These per-scene `.txt` files are the normalized story text the rest of the flow reads: by default they feed `02_beat_extraction` (shot division) directly; when the opt-in `screenplay_frontend` flag is on, they feed `02_1_screenplay`, whose output `02_2_scene_extraction` re-segments into film scenes (so marker-based splitting here is retained but no longer the authoritative scene source in that mode).

## I/O

- Input: `inputs/manuscript.txt` (raw manuscript), `run_config.yaml` (marker convention, POV character).
- Output: `outputs/<scene_id>.txt` per scene, plus `outputs/scenes_manifest.json` listing scene order, chapter number, and any inline metadata.

## Run / test instructions

Implemented (no Coordinator yet, so run directly):

```
python -m pytest stages/01_manuscript_ingestion/tests/ -v

python stages/01_manuscript_ingestion/src/run.py \
  stages/01_manuscript_ingestion/inputs \
  stages/01_manuscript_ingestion/outputs \
  <path-to-run_config.yaml>
```

`inputs/manuscript.txt` + a `run_config.yaml` with `scene_marker_convention` are required. `run.py` also exposes `main(input_dir, output_dir, run_config)` and a `run_from_envelope(envelope)` adapter for future Coordinator use (`CLAUDE.md` §9).

## Numeric pass criterion

100% of declared markers found; every resulting scene file round-trips (concatenating all scene files reproduces the original manuscript minus marker lines, byte-for-byte).

**Result (2026-07-14, against `shared/fixtures/sample_scene.txt`): PASS.** 1 chapter, 1 scene found and split; scene body byte-for-byte matches the source minus the two marker lines. 5/5 unit tests pass (basic split, multi-scene/multi-chapter synthetic manuscript, the standing fixture, missing-manuscript `FAILED` path, no-markers-found `NEEDS_INPUT` path).

## Review checklist

- [x] Fixture scene (`shared/fixtures/sample_scene.txt`) splits cleanly into the expected chapter/scene files.
- [x] No marker misses or off-by-one boundary errors (verified via synthetic 2-chapter/3-scene fixture in tests).
- [x] Output is valid UTF-8 regardless of input encoding (`utf-8-sig` read tolerates a BOM, `utf-8` write).
- [ ] Human review of real `outputs/` artifacts — pending.
