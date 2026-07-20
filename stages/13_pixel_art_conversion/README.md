# 13_pixel_art_conversion

**Type:** CODE — see `CLAUDE.md` §2 and `ARCHITECTURE.md` §2.

## Purpose

Restyles the QA-approved `final.mp4` into a retro pixel-art look: area-averaged downscale to a pixel grid, palette reduction with ordered (bayer) dithering, an edge-detected dark outline overlay on real object boundaries, nearest-neighbor upscale back to source resolution. Emits `final_pixel_art.mp4` as an alternate cut alongside the original — this stage never overwrites or replaces `final.mp4`.

This is a pure visual restyle with no creative judgment left to make, arrived at over three rounds of human review against real ffmpeg-only samples (see `DECISIONS_LOG.md` and `ARCHITECTURE.md` change log): (1) 2026-07-18, human picked a palette-limited dithered look over plain nearest-neighbor block and ffmpeg's `pixelize` filter; (2) same day, human reported object outlines unclear in places — root cause was the downscale itself using nearest-neighbor point-sampling (one arbitrary pixel per block instead of the region's true average), fixed by switching to area averaging and adding an edgedetect-based outline overlay (two more variants, softer and bolder/dilated, were tried and rejected); (3) same day, human still found some areas hard to follow even with the outline and asked to reduce the pixel size — tried three finer grids (240x136, 320x180, 480x270) and picked 320x180 as the best balance of legibility vs. still looking like pixel art (480x270 started reading as a photo with an outline filter). Everything from here is mechanical.

## I/O

- Input: `inputs/final.mp4` (the Stage 12-approved final render).
- Output: `outputs/final_pixel_art.mp4`.
- Config: `config/pixel_art_spec.yaml` (downscale factor, palette size, dither method/scale, edge-detection thresholds — no magic numbers in code), `config/render.yaml` (codec/bitrate, reused from Stage 11), `config/thresholds.yaml`'s `pixel_art.duration_tolerance_pct`.

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

**Result (2026-07-18, against the real run's `final.mp4`, 320x180 grid + outline version):** PASS. Source 65.067s → output 65.067s duration, grid computed from the source's real resolution, 32-color bayer-dithered palette plus outline overlay. 3/3 unit tests pass (happy path on synthetic color+sine fixture, missing-input hard fail, grid-computation rounding).

## Review checklist

- [x] `final_pixel_art.mp4` visually reviewed (frames pulled at multiple timestamps, not just the first shot; zoomed crops on the hardest boundary region) — outline overlay makes previously-ambiguous object silhouettes clearly readable, no artifacts or freezes.
- [x] Original `final.mp4` untouched — this stage produces a separate output file, never overwrites Stage 11/12's artifacts.
- [x] All tunables (grid factor, palette size, dither method/scale, edge-detection thresholds) come from `config/pixel_art_spec.yaml`, never hardcoded.
- [x] Outline overlay preserves color (only the luma channel is darkened at edges) — verified after catching a real bug where a naive `blend` filter desaturated the entire frame to grayscale by also multiplying the chroma planes.
