# 05_retrieval_verification

**Type:** CODE + HITL — see `CLAUDE.md` §2 and `ARCHITECTURE.md` §2.

## Purpose

Downloads the top-k candidates per beat (`config/thresholds.yaml`'s `retrieval_verification.top_k`), samples frames across the actual clip (not just the thumbnail), and re-scores. Beats whose top candidates are within the close-score margin are batched for a human tie-break via the Coordinator (`CLAUDE.md` rule 10) rather than auto-selected.

## I/O

- Input: `inputs/beats.json` (for `visual_description` text) + `inputs/candidates.json` (Stage 04's output — only beats with `routing.route == "05_retrieval_verification"` are this stage's concern; others are skipped).
- Output: `outputs/assets_manifest.json` entries with `origin: retrieved_verified` for winning beats, only written when every beat is resolved. A `FALLBACK_ROUTED` response (reason: `verification_failed`) covers beats where every top-k candidate failed to download/verify. A `NEEDS_INPUT` response (reason: `close_score_tiebreak`) batches every beat still ambiguous after verification into one response, per `CLAUDE.md` rule 10 — no auto-select on a close call.

## Run / test instructions

Implemented, using `shared/media/` (ffmpeg frame extraction) + `shared/embeddings/` (CLIP re-scoring):

```
python -m pytest stages/05_retrieval_verification/tests/ -v   # mocked downloader/frame-extractor/embedder, no network or ffmpeg

python stages/05_retrieval_verification/src/run.py \
  stages/05_retrieval_verification/inputs \
  stages/05_retrieval_verification/outputs \
  <path-to-run_config.yaml>
```

`main(input_dir, output_dir, run_config, downloader=None, frame_extractor=None, embedder=None, thresholds=None, hitl_decisions=None)` — `hitl_decisions` (`{beat_id: chosen_candidate_id}`) is how a second invocation resolves beats a human already picked from a prior `NEEDS_INPUT` response; the stage is a pure function of its inputs each call, so re-running is always safe (downloaded videos/frames are cached in `shared/runs/<run_id>/cache/videos/`).

## Numeric pass criterion

0 beats within the close-score margin auto-selected without a HITL round-trip. Verification re-score computation is deterministic given the same downloaded frames.

**Result (2026-07-14, against Stage 04's real 25-candidate/5-beat output): PASS.** All 5 beats downloaded (top-3 candidates each, real Pexels mp4s), frame-sampled (3 evenly-spaced frames via ffmpeg), and re-scored with CLIP — the full download→extract→re-score path, not mocked. First pass: **all 5 beats came back `NEEDS_INPUT`**, including beat b001 which Stage 04 had flagged `retrievable: high` off thumbnail-only scoring — verification against actual video frames found its top two candidates only 0.012 apart, inside the 0.03 margin. This is the mechanism working exactly as designed (Rule 10: never auto-select a close call), not a bug. 0/5 auto-selected without review — criterion met. Human tie-break decisions recorded in `DECISIONS_LOG.md`; re-running with `hitl_decisions` resolved all 5 to `COMPLETE` — `outputs/assets_manifest.json` has 5 verified assets with real creator attribution (cottonbro studio, VY.YV C&D, Yaroslav Shuraev, Eky Rima Nurya Ganda, RDNE Stock project) and real cached video files (275KB-4.2MB each) under `shared/runs/run_2026_07_ch1_test/cache/videos/`. 7/7 unit tests pass (clear-margin auto-select, close-margin tie-break, HITL-decision resolution, all-candidates-fail fallback, single-candidate no-margin-check, non-05-routed beats skipped, missing-input `FAILED` path).

## Review checklist

- [x] Downloaded candidate files are cached correctly under `shared/runs/<run_id>/cache/videos/` (re-run doesn't re-download).
- [x] HITL batching presents all close-score beats for one scene together, not one at a time (all 5 came back in a single `NEEDS_INPUT` response).
- [x] Human tie-break decisions recorded in `DECISIONS_LOG.md` and correctly resolved on re-run.
- [ ] Human review of real `outputs/assets_manifest.json` — pending.
