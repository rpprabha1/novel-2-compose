# 04_clip_reranking

**Type:** CODE — see `CLAUDE.md` §2 and `ARCHITECTURE.md` §2.

## Purpose

Computes CLIP embedding similarity between each candidate's thumbnail/keyframe and its beat's `visual_description` (via `shared/embeddings/`), then routes each beat: above `config/thresholds.yaml`'s `clip_reranking.similarity_cutoff` to 05, below to 06, within `close_score_margin` flagged `retrievable: low` for human tie-break in 05.

## I/O

- Input: `inputs/beats.json` (from 02, for `visual_description` text) + `inputs/candidates.json` (from 03).
- Output: `outputs/candidates.json` with `similarity_score` populated per candidate + a `routing` object per beat (`route`/`best_score`/`retrievable` — schema extended in `candidates.schema.json`, logged in `ARCHITECTURE.md`).

## Run / test instructions

Implemented, using `shared/embeddings/` (HuggingFace CLIP, `config/embeddings.yaml`):

```
python -m pytest stages/04_clip_reranking/tests/ -v   # mocked embedder, no model load

python stages/04_clip_reranking/src/run.py \
  stages/04_clip_reranking/inputs \
  stages/04_clip_reranking/outputs \
  <path-to-run_config.yaml>
```

`main(input_dir, output_dir, run_config, embedder=None, thresholds=None)` takes an injectable embedder for testing. Embeddings are cached to `shared/runs/<run_id>/cache/embeddings/*.npy`, keyed by content hash **and** a `CACHE_VERSION` in `shared/embeddings/clip_backend.py` — bump that constant if the embedding computation logic ever changes shape/meaning, or stale cached vectors get silently reused (this happened once during development: a transformers-version quirk meant `get_text_features()`/`get_image_features()` returned a `BaseModelOutputWithPooling` object instead of a plain tensor, and the first buggy run's bad-shaped output got cached before the fix, silently reappearing on the next run until the cache was cleared).

## Numeric pass criterion

100% of beats routed (none left unclassified); score computation matches `thresholds.yaml` cutoffs exactly, with no hardcoded threshold values in code.

**Result (2026-07-14, `openai/clip-vit-base-patch32`, against Stage 03's real 25-candidate output): PASS.** All 5 beats routed (0 unclassified): 5/5 to `05_retrieval_verification` (1 `high`, 4 `low`/HITL-flagged — scores clustered 0.27-0.33, close to the 0.28 cutoff, which tracks: general stock footage rarely scores far above a specific narrative description), 0/5 to `06_fallback_generation` this round. Spot-checked top matches by hand: b001 (staircase) top hit is literally titled "conceptual woman going upstairs"; b002 (attic/furniture) top hit is "exploring an abandoned attic room indoors"; b004 (cat) top hit is "relaxed orange cat lounging on the floor" — reranking is doing real work over Stage 03's noisier raw keyword results. 7/7 unit tests pass (high/low/fallback routing boundaries, zero-candidates edge case, a broken-thumbnail failure scored worst-case without crashing the stage, missing-input and unknown-beat-id `FAILED` paths).

## Review checklist

- [x] Spot-checked several scores against manual judgment — all top matches are visually sensible for their beat.
- [x] Routing logic reads thresholds from `config/thresholds.yaml`, never hardcodes them (unit-tested with a distinct custom threshold set to catch accidental hardcoding).
- [x] `close_score_margin` beats are flagged, not silently auto-routed either direction (unit-tested).
- [ ] Human review of real `outputs/candidates.json` — pending.
