# 02_2_scene_extraction

**Type:** HYBRID (AGENT segmentation + CODE manifest/render) — see `CLAUDE.md` §2 and `ARCHITECTURE.md`.

## Purpose

Segments one source passage's **screenplay** (from `02_1_screenplay`) into
distinct **film scenes** and emits the canonical `scenes_manifest.json` + one
`.txt` per scene that the rest of the pipeline consumes. Second of the three
front-end decomposition agents (screenplay → scene extraction → shot division).

Scene splitting used to be pure CODE in `01_manuscript_ingestion` (marker-based).
This stage adds agent judgment so scenes are found from the screenplay's
sluglines — including sub-scene boundaries the author's chapter/scene markers
never marked. The CODE→AGENT reclassification is logged in `ARCHITECTURE.md`.

- **AGENT half:** decide scene boundaries, write each scene's heading/summary,
  render its action+narration into readable prose.
- **CODE half:** validate the segmentation, assign `scene_id` / chapter / scene
  numbers / file paths, write the `.txt` files, build a schema-valid manifest.

Runs at scene granularity behind the opt-in `screenplay_frontend` run-config
flag (default off).

## I/O

- **Input:** `inputs/screenplay.json` (from `02_1_screenplay`) + `run_config`.
- **Output:** `outputs/scenes_manifest.json` (validates against
  `shared/schemas/scenes_manifest.schema.json`) + one `outputs/<scene_id>.txt`
  per segmented scene. The agent's intermediate segmentation validates against
  `shared/schemas/scene_segmentation.schema.json`.

Scene ids: a single film scene keeps the source `scene_id` (e.g. `ch1_sc1`);
multiple film scenes are suffixed `_p1`, `_p2`, … so they stay unique.

## Run / test instructions

```
python stages/02_2_scene_extraction/src/run.py <input_dir> <output_dir> <run_config.yaml>
python -m pytest stages/02_2_scene_extraction/tests/
```

Tests inject a fake `agent_call`; no live model needed.

## Numeric pass criterion

Agent output validates against `scene_segmentation.schema.json`; the built
`scenes_manifest.json` validates against `scenes_manifest.schema.json`; 100% of
scenes have non-empty `text` (not equal to their heading); a single passage
yielding >6 scenes is HITL-flagged as possible over-splitting.

## Review checklist

- [ ] Scene boundaries match real location/time changes in the screenplay.
- [ ] Every screenplay element is reflected in some scene's text (no drops).
- [ ] Scene `.txt` files are readable prose, not bare sluglines.
- [ ] `scenes_manifest.json` scene_ids are unique and downstream-consumable.

## Agent prompt

`AGENT_PROMPT.md` (all 12 `CLAUDE.md` §7 sections) is the source of truth;
`src/run.py` renders sections 1-6 and 9 into the system prompt.
