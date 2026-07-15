# 10_human_review_gate

**Type:** CODE + human — see `CLAUDE.md` §2 and `ARCHITECTURE.md` §2. No agent involvement.

## Purpose

Generates a contact-sheet HTML (thumbnail grid + timeline scrub markers + audio cue markers) from `timeline.json` and `audio_mix.json` for human review. The human approves (writing `outputs/APPROVED.md`) or requests changes, which route back through the Coordinator to the relevant upstream stage.

## I/O

- Input: `inputs/timeline.json`, `inputs/audio_mix.json`.
- Output: `outputs/contact_sheet.html`; `outputs/APPROVED.md` written by the human (or Claude on explicit instruction) on approval.

## Run / test instructions

Implemented — pure CODE, no agent, no external services beyond ffmpeg for thumbnail extraction:

```
python -m pytest stages/10_human_review_gate/tests/ -v   # fake thumbnail extractor, real tiny synthetic frames

python stages/10_human_review_gate/src/run.py \
  stages/10_human_review_gate/inputs \
  stages/10_human_review_gate/outputs \
  <path-to-run_config.yaml>
```

`main(input_dir, output_dir, run_config, thumbnail_extractor=None)` — the extractor is injectable for testing. Thumbnails are the frame at the midpoint of each clip's trimmed `[source_in_s, source_out_s]` window (not the source file's own midpoint, which could be anywhere in a much longer stock clip), cached under `shared/runs/<run_id>/cache/thumbnails/` and embedded as base64 so `contact_sheet.html` is a single self-contained file. Cross-references `shared/runs/<run_id>/manifest.json` (by matching the asset/track ref) to show license/creator per shot and music cue, best-effort — absent if the manifest doesn't have an entry.

## Numeric pass criterion

Contact sheet renders 100% of shots and cue markers with 0 missing thumbnails.

**Result (2026-07-15, against the real scene's timeline.json + audio_mix.json, post-Stage-08-reconciliation): PASS.** 5/5 shot thumbnails rendered (spot-checked two by hand: b001's staircase thumbnail and b002's fallback-generated trunk/latch thumbnail are both genuinely representative, on-theme frames), 5 narration stems and 1 music cue listed with license/creator resolved from the run manifest. 5/5 unit tests pass (happy path, thumbnail-failure `FAILED`, missing-input `FAILED`, HTML-escaping of untrusted content, zero-music-stems no-crash) — including a regression test for the same cross-test cache-isolation bug found in Stage 09 (fixed here proactively before it could bite).

## Review checklist

- [x] Sheet accurately reflects `timeline.json` — no stale cache of a prior version (thumbnails keyed by sanitized `shot_id`, timeline/audio data read fresh each run).
- [ ] Approval/rejection routes back through the Coordinator, never edits stage outputs directly — N/A until a Coordinator exists; approval is manual (`outputs/APPROVED.md`) for now.
- [x] Untrusted string fields (shot IDs, track refs, etc.) are HTML-escaped before rendering.
