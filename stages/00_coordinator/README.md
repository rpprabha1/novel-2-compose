# 00_coordinator

**Type:** AGENT (orchestration judgment) + CODE core — see `CLAUDE.md` §2 and `ARCHITECTURE.md` §2.

## Purpose

The only component that invokes stages, moves data between them, talks to the human, and holds run-level state. Sequencing, envelope construction, and gate enforcement are code; framing `NEEDS_INPUT`/HITL questions for the human is agent work. Full protocol: `CLAUDE.md` §5.

## I/O

- Input: `shared/runs/<run_id>/run_config.yaml` (created at run start).
- Output: Task Envelopes to every stage (schema: `shared/schemas/task_envelope.schema.json`), `shared/runs/<run_id>/coordinator_log.jsonl` (append-only), human-facing HITL summaries.

## Canonical per-scene order

Footage = source-free downloader lane; audio runs before final shot tiling because narration length is authoritative (see `ARCHITECTURE.md` / `DECISIONS_LOG.md`):

```
02 beat/shot division
  -> [01_1 download -> downloader_manifest -> 01_2 scene_scoring]
  -> 09 audio_production -> 07_2 narration_shot_mapping -> 08 timeline_builder
  -> 10 human_review_gate  (gate: APPROVED.md required before 11)
  -> 11 assembly_render -> 12 qa_attribution -> 13 pixel_art -> 14 anime_style
```

Retired stages `03/04/05/06/07` stay in-tree, not invoked. The `01_1_downloader`
is an opaque black box — only its CLI + `outputs/` are used, never its code
(`CLAUDE.md` §0a).

## Run / test instructions

```
python stages/00_coordinator/src/run.py [scene_id ...]   # e.g. ch1_sc1
python run_full_novel.py [scene_id ...]                  # repo-root shim, delegates here
python -m pytest stages/00_coordinator/tests/            # synthetic-fixture core tests
```

The orchestration logic lives in `src/run.py` (the `Coordinator` class + the
per-scene `run_scene`). The repo-root `run_full_novel.py` is a thin shim that
delegates here. Run config: `shared/runs/animal_farm_ch1/run_config.yaml`.

## Numeric pass criterion

Zero invocations of Stage N+1 without `outputs/APPROVED.md` present on Stage N. 100% of Stage Responses schema-validated against `expected_output_schema` before acceptance. Zero direct stage-to-stage handoffs (every artifact passes through the Coordinator).

## Review checklist

- [ ] Gate enforcement verified with a deliberately-unapproved stage (must refuse).
- [ ] `coordinator_log.jsonl` captures every envelope and response.
- [ ] HITL batching presents options + tradeoffs, never a raw dump.
