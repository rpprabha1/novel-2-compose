# 03_candidate_fetch

**Type:** CODE — see `CLAUDE.md` §2 and `ARCHITECTURE.md` §2.

## Purpose

For each beat, queries every enabled `FootageSource` (`shared/sources/`) with search terms derived mechanically from the beat's `visual_description`. Handles caching, rate limiting, and captures license/attribution at fetch time into the run manifest (`CLAUDE.md` rule 12).

## I/O

- Input: `inputs/beats.json` (from 02).
- Output: `outputs/candidates.json` (schema: `shared/schemas/candidates.schema.json`), manifest entries appended to `shared/runs/<run_id>/manifest.json`.

## Run / test instructions

Implemented — `PexelsSource`/`PixabaySource` (`shared/sources/`) need `PEXELS_API_KEY`/`PIXABAY_API_KEY` in `config/.env` (copy from `config/.env.example`).

```
python -m pytest stages/03_candidate_fetch/tests/ -v   # mocked FootageSource, no network

python stages/03_candidate_fetch/src/run.py \
  stages/03_candidate_fetch/inputs \
  stages/03_candidate_fetch/outputs \
  <path-to-run_config.yaml>
```

`inputs/beats.json` (Stage 02's output, copied in) is required. `main(input_dir, output_dir, run_config, sources=None, max_results=None)` takes an injectable `sources` dict for testing; the default builds `PexelsSource`/`PixabaySource` from whatever API keys are found in `config/.env` or the environment. Search terms are extracted mechanically (`extract_search_terms()` — stopword removal, no LLM call). Results are cached per `(source, query)` within a run; manifest entries are de-duplicated by `entry_id` before being appended to `shared/runs/<run_id>/manifest.json`.

## Numeric pass criterion

100% of API calls stay within source rate limits (0 uncaught 429s); every candidate entry has `source` and `license` populated; 0 candidates from a source not listed in `LICENSES.md`.

**Result (2026-07-14): mocked-test PASS, 6/6 unit tests** (basic extraction, complete path with a fake source, query caching verified via call-count assertion, a failing source degrades gracefully instead of crashing the stage, missing-`beats.json` `FAILED` path, no-sources-configured `NEEDS_INPUT` path).

**Live run (2026-07-14, Pexels only — no Pixabay key added), against Stage 02's real `beats.json`: PASS.** 25 candidates fetched across 5 beats (5 each), 0 API failures, every candidate has `source`+`license` populated, `shared/runs/run_2026_07_ch1_test/manifest.json` has 25 de-duplicated entries with creator/license/source_url. Relevance is intentionally noisy at this stage (e.g. "dog climbing down steps" showed up for the attic-staircase beat) — Pexels' keyword search is a loose match on the mechanically-extracted query string, and filtering for actual visual relevance is Stage 04's job (CLIP similarity), not this stage's.

## Review checklist

- [x] Cache prevents duplicate fetches for the same query within a run (unit-tested via call-count assertion).
- [x] Every candidate's license field is populated from the real API response, not assumed — confirmed on live run.
- [x] Search terms are derived mechanically (no agent call in this stage).
- [ ] Human review of real `outputs/candidates.json` — pending.
