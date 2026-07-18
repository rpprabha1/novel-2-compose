# 13_pixel_art_conversion

**Type:** CODE — see `CLAUDE.md` §2 and `ARCHITECTURE.md` §2.

## Purpose

Restyles the QA-approved `final.mp4` into a retro pixel-art look: nearest-neighbor downscale to a pixel grid, palette reduction with ordered (bayer) dithering, nearest-neighbor upscale back to source resolution. Emits `final_pixel_art.mp4` as an alternate cut alongside the original — this stage never overwrites or replaces `final.mp4`.

This is a pure visual restyle with no creative judgment left to make: the human already reviewed 3 real ffmpeg-only sample techniques rendered from the real `final.mp4` (plain nearest-neighbor block, ffmpeg's `pixelize` filter, this palette-limited dithered approach) and picked this one (2026-07-18, see `DECISIONS_LOG.md` and `ARCHITECTURE.md` change log). Everything from here is mechanical.

## I/O

- Input: `inputs/final.mp4` (the Stage 12-approved final render).
- Output: `outputs/final_pixel_art.mp4`.
- Config: `config/pixel_art_spec.yaml` (downscale factor, palette size, dither method/scale — no magic numbers in code), `config/render.yaml` (codec/bitrate, reused from Stage 11), `config/thresholds.yaml`'s `pixel_art.duration_tolerance_pct`.

## Run / test instructions

Implemented — pure CODE, no agent, ffmpeg only (`shared/media/pixel_art.py`):

```
python -m pytest stages/13_pixel_art_conversion/tests/ -v

python stages/13_pixel_art_conversion/src/run.py \
  stages/13_pixel_art_conversion/inputs \
  stages/13_pixel_art_conversion/outputs \
  <path-to-run_config.yaml>
```

The pixel grid is computed from `final.mp4`'s own resolution divided by `downscale_factor` (rounded to the nearest even number, minimum 2x2) rather than a fixed absolute size, so this works on any source resolution (CLAUDE.md rule 11).

## Numeric pass criterion

`final_pixel_art.mp4` must exist with duration within `thresholds.yaml`'s `pixel_art.duration_tolerance_pct` (default 1%) of the source `final.mp4` — this stage only re-filters frames, it never cuts or re-times, so drift beyond that tolerance means a real bug, not an expected effect (same reasoning as Stage 11's duration reconciliation check).

**Result (2026-07-18, against the real run's `final.mp4`):** PASS. Source 65.067s → output 65.067s (0.000% drift), grid computed from the source's real resolution, 32-color bayer-dithered palette. 3/3 unit tests pass (happy path on synthetic color+sine fixture, missing-input hard fail, grid-computation rounding).

## Review checklist

- [x] `final_pixel_art.mp4` visually reviewed (frames pulled at multiple timestamps, not just the first shot) — dithered pixel-art look holds up across different footage, no artifacts or freezes.
- [x] Original `final.mp4` untouched — this stage produces a separate output file, never overwrites Stage 11/12's artifacts.
- [x] All tunables (grid factor, palette size, dither method/scale) come from `config/pixel_art_spec.yaml`, never hardcoded.
