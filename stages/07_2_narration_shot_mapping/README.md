# 07_2_narration_shot_mapping

**Type:** CODE — see `CLAUDE.md` §2 (ffmpeg extraction + tiling arithmetic is deterministic, like `08_timeline_builder`). No agent. Added 2026-07-24 (author request; see `ARCHITECTURE.md` change log).

## Purpose

The downloader lane's clips are often long (a 3-minute video for what a beat needs ~4 seconds of). This stage performs the **narration-to-shot mapping**: for each beat it takes that beat's best-matching downloader clips (from `01_2_scene_scoring`'s ranking), tiles the beat's **narration duration** (from `09_audio_production`) into short ~shot-length windows walking distinct positions across those clips, and **physically extracts** each window as its own short `.mp4` (via `shared/media.trim_clip`). A long clip yields real visual progression (successive windows), and a beat's multiple ranked clips alternate — never a single frozen hold.

Shot length defaults to the active pacing preset's `hold_duration_s.max` (`config/editorial_vocab.yaml`; 4.0s for `standard`), overridable via `config/thresholds.yaml` → `shot_extraction.target_shot_length_s`. If a beat's narration would need more than `shot_extraction.max_shots_per_beat` (≤12, the `edit_plan` schema ceiling) shots, shot length is stretched to fit.

## I/O

- Input: `inputs/beats.json`, `inputs/scene_scores.json` (`01_2_scene_scoring`'s per-beat ranking), `inputs/downloader_manifest.json` (clip durations + file_refs), and optionally `inputs/audio_mix.json` (`09_audio_production`'s narration durations — falls back to each beat's `est_duration_s` if absent).
- Output:
  - `outputs/shot_map.json` — the explicit narration→shot mapping (`shared/schemas/shot_map.schema.json`): per beat, the ordered shots with source clip, source window, extracted file, and the narration span each covers.
  - `outputs/assets_manifest.json` — each extracted shot as a **source-free** asset (`origin: "downloader"`, neutral license, no creator), ready for `08_timeline_builder`.
  - `outputs/edit_plan.json` — each beat's shots in order (`in_s=0`, `out_s`=`hold_duration_s`=the shot's true ffprobed duration), `transition_out: "hard-cut"`. Supersedes `07_editorial_direction` for the downloader lane.
  - Extracted shot files in `shared/runs/<run_id>/cache/shots/`.

## Run / test instructions

`main(input_dir, output_dir, run_config, trim=None, prober=None, thresholds=None, vocab=None)` — `trim`/`prober` are injectable so tests never call ffmpeg. Source-free by design (imports the downloader lane's `DOWNLOADER_LICENSE`/`DOWNLOADER_SOURCE`).

```
python -m pytest stages/07_2_narration_shot_mapping/tests/ -v   # mocked trim + probe, no ffmpeg

python stages/07_2_narration_shot_mapping/src/run.py \
  stages/07_2_narration_shot_mapping/inputs \
  stages/07_2_narration_shot_mapping/outputs \
  <path-to-run_config.yaml>
```

A beat with no scored clip, or whose every planned shot fails to extract, is routed `FALLBACK_ROUTED` (reason `no_scored_clip` / `shot_extraction_failed`) and contributes no shots — never a silent drop (CLAUDE.md rule 7). If *no* beat produces a shot, the stage returns `FAILED`.

## Numeric pass criterion

Every beat that has at least one scored clip produces ≥1 extracted shot, and the sum of a beat's shot durations covers its narration duration to within one `shot_extraction.min_shot_length_s` (the only shortfall permitted is when a beat's entire available footage, across `assets_per_beat` clips and `_MAX_WINDOW_REUSE_PASSES` reuse passes, is genuinely shorter than the narration). Every extracted shot's `hold_duration_s` equals its true ffprobed duration (so `08_timeline_builder`'s fit checks pass exactly). `target_shot_length_s`/`min_shot_length_s`/`max_shots_per_beat` are read from config, never hardcoded (unit-tested).

## Review checklist

- [x] Shots are physically extracted short clips, not references into the long source (verifiable: `outputs/` files exist and are ~shot-length).
- [x] Distinct windows per shot — a long clip advances its cursor; multiple clips alternate (unit-tested on the tiling planner).
- [x] Output is source-free — `assets_manifest`/`shot_map` carry no platform/url/creator/license (only `clip_id`/`file_ref`).
- [x] `hold_duration_s`/`out_s` equal the extracted file's true duration, so Stage 08 never fails its `hold ≤ out_s` / `≤ asset duration` checks.
- [ ] Human review of a real `shot_map.json` + rendered shots — pending.
