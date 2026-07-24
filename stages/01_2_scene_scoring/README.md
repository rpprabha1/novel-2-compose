# 01_2_scene_scoring

**Type:** CODE — see `CLAUDE.md` §2 (CLIP embedding + cosine scoring is math, classified CODE like `04_clip_reranking`). No agent.

## Purpose

Scores the downloader stage's clips (`01_1_downloader`) against the scene, **per beat**. For each beat it embeds the beat's `visual_description`, samples frames from every downloaded clip, embeds those frames, and averages their CLIP cosine similarity to the beat text — then ranks the clips best-fit-first for that beat.

Output is **ranked scores only**: every clip that yielded frames appears with its score and rank for each beat. No single clip is selected as a winner and nothing is routed — a later stage (or a human) makes the pick.

The output is **source-free by design**: consistent with the downloader lane, it carries no `source`, `origin`, `url`, `license`, or `creator` — only a neutral `clip_id`, a `file_ref` to locate the clip, and the `score`/`rank`. The output schema (`shared/schemas/scene_scores.schema.json`) forbids source fields via `additionalProperties: false`.

## I/O

- Input: `inputs/beats.json` (from `02_beat_extraction`, for each beat's `visual_description`) + `inputs/downloader_manifest.json` (from `01_1_downloader`, via `shared/downloader_manifest.py`, for the clip list + `file_ref`s).
- Output: `outputs/scene_scores.json` — one entry per beat with a `ranked_clips` list (`clip_id`, `file_ref`, `score`, `rank`, `frames_scored`), validated against `shared/schemas/scene_scores.schema.json`.
- Frames are extracted once per clip (via `shared/media.extract_frames`) and their embeddings reused across all beats — a clip is never re-sampled or re-embedded per beat.

## Run / test instructions

Uses `shared/embeddings/` (HuggingFace CLIP, `config/embeddings.yaml`) and `shared/media/` (ffmpeg frame sampling). Frames per clip: `config/thresholds.yaml` → `scene_scoring.frames_per_clip`.

```
python -m pytest stages/01_2_scene_scoring/tests/ -v   # mocked embedder + extractor, no model load, no ffmpeg

python stages/01_2_scene_scoring/src/run.py \
  stages/01_2_scene_scoring/inputs \
  stages/01_2_scene_scoring/outputs \
  <path-to-run_config.yaml>
```

`main(input_dir, output_dir, run_config, frame_extractor=None, embedder=None, thresholds=None, clips_base_dir=None)` takes an injectable embedder and frame extractor for testing. Clip `file_ref`s resolve in order: staged into `inputs/` by basename, then under `clips_base_dir` (repo root by default), then as an absolute path; a clip that resolves to nothing is excluded and counted, never fatal.

## Numeric pass criterion

100% of beats appear in the output, each with a ranking that covers every clip that yielded frames; within each beat, `rank` is a contiguous `1..N` permutation ordered by non-increasing `score`. No clip is silently dropped — the only exclusion is a clip that could not be frame-sampled, which is counted and reported in the summary. `frames_per_clip` is read from `config/thresholds.yaml`, never hardcoded (unit-tested with a custom value).

**Result (2026-07-23, `openai/clip-vit-base-patch32`, real 3-clip download for "a crow throwing stones in bottle" against a synthetic 2-beat scene): PASS.** Both beats scored against all 3 clips (3 frames each, real ffmpeg sampling); every ranking contiguous `1..3` and score-ordered. Spot check: for both beats the top-ranked clip is "Causal understanding of water displacement by a crow" (b001 0.331, b002 0.308) — the clip that most literally shows a crow raising water with stones — over the two nursery-rhyme retellings. Scores cluster 0.25–0.33, matching stage 04's observation that general footage scores in that band against specific narrative text. 7/7 unit tests pass on synthetic fixtures (deterministic ranking, config-driven frame count, frame-extraction failure excluded-and-counted, missing-clip-file excluded, missing-input `FAILED`, empty-beats / empty-clips edge cases).

## Review checklist

- [x] Ranking is per-beat and best-fit-first; ranks contiguous and score-ordered (unit-tested).
- [x] `frames_per_clip` read from `config/thresholds.yaml`, never hardcoded (unit-tested with a distinct value).
- [x] Output is source-free — schema rejects any source/origin/url/license field (`additionalProperties: false`).
- [x] Frame-sampling failure excludes-and-counts the clip rather than crashing (unit-tested).
- [ ] Human review of a real `outputs/scene_scores.json` — pending.
