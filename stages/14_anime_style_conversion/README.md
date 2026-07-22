# 14_anime_style_conversion

**Type:** CODE — see `CLAUDE.md` §2 and `ARCHITECTURE.md` §2.

## Purpose

Restyles the QA-approved `final.mp4` into an anime look via AnimeGANv2 (a pretrained GAN, MIT-licensed, vendored under `shared/models/animegan/` — see `LICENSE_animegan2-pytorch.txt` there). Emits `final_anime.mp4` as an alternate cut alongside the original — this stage never overwrites or replaces `final.mp4` or `final_pixel_art.mp4`.

This is a pure restyle with no creative judgment left to make. The human picked the checkpoint (`paprika`, over `celeba_distill`/`face_paint_512_v1`/`face_paint_512_v2`) after reviewing 4 real ~18s samples rendered from the real `ch1_sc1` `final.mp4` — see `DECISIONS_LOG.md` and `ARCHITECTURE.md` change log. A second, separate decision: AnimeGANv2 visibly blurs text-card beats (the fallback lane's plain-text visuals) toward illegibility since the model is trained on photographic/portrait content, not flat text-on-solid-background graphics — the human explicitly chose to apply the style uniformly anyway rather than mask fallback-origin clips out of the stylization pass.

Frame-rate handling is not a quality shortcut so much as a real feasibility constraint made into a real animation convention: single-frame inference on this CPU-only dev machine measured ~2-3s/frame at 960px width (full extract/style/reassemble pipeline, not just raw model compute) — a full chapter-length video at native 30fps would take many hours per chapter. `config/anime_style_spec.yaml`'s `stylize_fps` (6) trades this down to a tractable per-chapter cost by only running inference on a fraction of the frames and holding each styled frame across several output frames at `output_fps` (30) — "limited/on-twos animation" is standard practice in real anime production, not just an expedient here, and this pipeline's content (slow Ken Burns holds, static text cards) shows almost no visible difference at a reduced stylization rate.

## I/O

- Input: `inputs/final.mp4` (the Stage 12-approved final render).
- Output: `outputs/final_anime.mp4`.
- Config: `config/anime_style_spec.yaml` (checkpoint name, device, target width, stylize/output fps — no magic numbers in code), `config/render.yaml` (codec/bitrate, reused from Stage 11), `config/thresholds.yaml`'s `anime_style.duration_tolerance_pct`.
- Model: `shared/models/animegan/{model.py, paprika.pt}` (vendored architecture + checkpoint; other three reviewed checkpoints are not shipped since only `paprika` was chosen).

## Run / test instructions

Implemented — pure CODE, real neural inference via `shared/generation/animegan_backend.py` (`stylize_video()`), ffmpeg for frame extraction/reassembly:

```
python -m pytest stages/14_anime_style_conversion/tests/ -v   # mocked stylizer, no real model inference

python stages/14_anime_style_conversion/src/run.py \
  stages/14_anime_style_conversion/inputs \
  stages/14_anime_style_conversion/outputs \
  <path-to-run_config.yaml>
```

`main()` also accepts an injectable `stylizer` callable matching `stylize_video()`'s signature (same DI pattern as Stage 06/07's injectable model calls) — this is how tests avoid a live model call per CLAUDE.md's fixture rule.

**Real per-chapter cost is substantial** — expect roughly 1.5-3 hours of CPU time per chapter at the shipped `stylize_fps`/`target_width` on comparable hardware. This is a real, load-bearing tradeoff (see Purpose), not a bug; plan bulk runs accordingly.

## Numeric pass criterion

`final_anime.mp4` must exist with duration within `thresholds.yaml`'s `anime_style.duration_tolerance_pct` (default 2% — looser than pixel_art's 1% since this stage re-times through a reduced-then-restored frame rate rather than a straight re-filter) of the source `final.mp4`.

**Result:** pending real end-to-end run against a full chapter's `final.mp4` — see `DECISIONS_LOG.md` for the latest real measurement once available.

## Review checklist

- [x] `final_anime.mp4` visually reviewed against real rendered samples at multiple points in the source video, not just the opening shot.
- [x] Original `final.mp4` untouched — this stage produces a separate output file, never overwrites Stage 11/12/13's artifacts.
- [x] All tunables (checkpoint, device, target width, stylize/output fps) come from `config/anime_style_spec.yaml`, never hardcoded.
- [x] Text-card legibility tradeoff under this style was surfaced to and explicitly decided by the human, not silently accepted.
- [ ] Full multi-chapter run timing confirmed practical before committing to the full novel.
