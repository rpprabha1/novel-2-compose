# 06_fallback_generation — RETIRED / DISABLED (2026-07-23)

**Status: DISABLED.** This stage is retired as of the 2026-07-23 downloader-lane cutover (author override; see `DECISIONS_LOG.md` and `ARCHITECTURE.md`'s change log). Its `src/run.py` is now a no-op that always returns `COMPLETE` and generates nothing. The folder is kept in place per the author's "retire, keep folders" decision; the prior implementation lives in git history.

**Why:** the pipeline now sources footage exclusively from the source-free downloader lane (`01_1_downloader` → `shared/downloader_manifest.py` → `01_2_scene_scoring` → `shared/downloader_assets.py`). The author's instruction was to "strictly remove the backup option" — the synthetic fallback is "not needed at all." Beats the downloader lane can't cover simply contribute no asset (no synthetic placeholder is produced).

Everything below this line is **historical record** of what the stage used to do, retained for context and for anyone reviving it from git history.

---

**Type (historical):** CODE by default, HYBRID/AGENT+diffusion opt-in — reclassified 2026-07-18 (see `ARCHITECTURE.md` change log).

## Purpose (historical)

For any beat with no acceptable retrieved asset. **Default CODE path (2026-07-18, visual technique updated 2026-07-23):** a Ken-Burns-animated mood-colored gradient — no text, no diffusion model — via `shared/media.generate_mood_visual()`. Reclassified from AGENT+diffusion after `sd-turbo` repeatedly exhausted RAM/disk loading on a constrained dev machine (6 real failed attempts, see `DECISIONS_LOG.md`). The default visual itself changed 2026-07-23 from an on-screen text card (the beat's own `visual_description`, rendered as literal text) to the current text-free gradient: `09_audio_production`'s TTS already speaks that same text aloud as narration, so displaying it again on screen was redundant and read as a broken slideshow, not video - the author flagged this directly on a real rendered chapter. **Original HYBRID path (still implemented, opt-in):** agent half writes an image-generation prompt from a beat's `visual_description`, tone, and mood tags — never inventing plot detail beyond the beat data; code half calls the `sd-turbo` generation backend and applies Ken Burns zoompan to turn a still into video-length footage.

## I/O

- Input: `inputs/beats.json` + `inputs/candidates.json` (Stage 04's output — only beats with `routing.route == "06_fallback_generation"` are this stage's concern).
- Output: `outputs/assets_manifest.json` entries with `origin: generated_fallback` (license text distinguishes `"Generated (mood visual)"` vs `"Generated (local stabilityai/sd-turbo)"`). AGENT mode additionally writes `outputs/fallback_prompt.json` (schema: `shared/schemas/fallback_prompt.schema.json`); CODE mode doesn't, since there's no agent output to persist.

## Run / test instructions

Implemented. CODE mode (default) uses only `shared/media.generate_mood_visual()` (plain ffmpeg, no ML). AGENT+diffusion mode (opt-in) additionally uses `shared/agents/` (Ollama, same as Stage 02) for the prompt-writing half and `shared/generation/` (local `diffusers`, `stabilityai/sd-turbo`) + `shared/media.ken_burns_zoompan()` for the render half:

```
python -m pytest stages/06_fallback_generation/tests/ -v   # both modes covered, mocked renderers, no model calls

python stages/06_fallback_generation/src/run.py \
  stages/06_fallback_generation/inputs \
  stages/06_fallback_generation/outputs \
  <path-to-run_config.yaml>
```

`main(input_dir, output_dir, run_config, agent_call=None, image_generator=None, zoompan=None, mood_visual_renderer=None)` — all externals are injectable for testing. `agent_call=None` (the CLI/test default) uses the CODE mood-visual path; pass an explicit `agent_call` (e.g. `agent_call=run._default_agent_call`) to use the original AGENT+diffusion path. Beats not routed here are silently skipped (0 output if none match — a valid `COMPLETE`, not an error).

## Numeric pass criterion

100% of routed beats produce a usable asset — no silent drops (`CLAUDE.md` rule 7: "no match" is a routed outcome, not a crash). Generated asset duration matches the beat's `est_duration_s` within `config/thresholds.yaml` tolerance.

**Result (2026-07-14, AGENT+diffusion mode, since superseded as the default): PASS.** The real pipeline scene never routes anything here (all 5 beats found usable footage in Stage 05), so this was exercised against a synthetic fixture beat (`stages/06_fallback_generation/inputs/`, not part of the real run) deliberately chosen to be hard to source as stock footage: *"a translucent, ghostly figure of a woman stands at the top of the attic stairs, slowly fading into mist."* Ollama (`llama3.2:3b`) wrote a well-grounded prompt (style modifiers and negative-prompt defaults applied verbatim from `config/visual_style.yaml`, no invented detail beyond the beat text); `sd-turbo` rendered a genuinely strong, on-tone still in ~35s; `ken_burns_zoompan` produced a 4.0s clip matching the beat's `est_duration_s` exactly (ffprobe-verified). 7/7 unit tests pass (no-beats-routed no-op, full generate path, invalid-JSON and prompt-count-mismatch `NEEDS_INPUT`, unsafe-keyword-screen `NEEDS_INPUT`, render-failure `FAILED`, missing-input `FAILED`).

Bug found and fixed along the way: the agent sometimes emits `"scene_id": null` explicitly rather than omitting the key, and `dict.setdefault()` doesn't touch a key that's already present-but-null — `outputs/assets_manifest.json` failed schema validation on the first real run. Fixed by force-overwriting `run_id`/`scene_id` from context instead of `setdefault()` (same latent bug fixed in Stage 02 too).

**Result (2026-07-18, CODE text-card mode, then the default): PASS.** Exercised for real on `ch1_sc1_b003`/`ch1_sc1_b005` (this run's actual beats needing fallback, after `sd-turbo` failed to load 6 times on this machine). `generate_text_card()` rendered both beats' `visual_description` as legible wrapped text over a solid background (visually confirmed by frame extraction), padded to `config/text_card.yaml`'s `min_duration_s` (20.0s) rather than each beat's raw `est_duration_s` (4.5s/3.0s) — needed for real: `08_timeline_builder`'s narration reconciliation required 14.78s/8.64s once real TTS narration length was known, which the raw estimate couldn't have covered. Downstream, the full 5-beat `final.mp4` (65.07s) now has real content end-to-end with zero freeze-frame gap, verified by checksum (all 11 shots distinct) and by extracting a frame at the very end of the timeline (63s, inside `b005`'s card) showing genuine text, not a frozen/black frame. 10/10 unit tests pass (3 new: default-CODE-mode asset generation, explicit-agent_call still routes to AGENT+diffusion mode, CODE-mode render-failure handling).

**Result (2026-07-23, CODE mood-visual mode, now the default — text card retired): PASS.** Author reviewed a real multi-chapter render and flagged the text-card approach directly: since `09_audio_production`'s Kokoro TTS already speaks each beat's `visual_description` aloud as narration, displaying that same text again on screen read as a broken slideshow, not video. Replaced `generate_text_card()` with `generate_mood_visual()` — a Ken-Burns-animated radial gradient colored by the beat's own `mood_tags` (`config/fallback_visual.yaml`'s `mood_color_map`), no text at all. `config/text_card.yaml` retired in favor of `config/fallback_visual.yaml`. 10/10 unit tests pass (updated to assert on `mood_tags`/gradient colors instead of rendered text).

## Review checklist

- [x] Generation prompts don't invent plot detail beyond what's in the beat (spot-checked the real run's `fallback_prompt.json` — AGENT mode).
- [x] Generated visuals are plausible for the run's `tone` (read from `run_config`, never inferred from beat text — CLAUDE.md rule 11 / genre-agnostic policy). CODE mode's text card sidesteps this entirely — it's the beat's own real text, not generated content.
- [x] `origin: generated_fallback` is tagged distinctly for QA (no attribution required, but flagged); license text distinguishes the two modes (`"Generated (mood visual)"` vs `"Generated (local stabilityai/sd-turbo)"`).
- [x] No on-screen text duplicates spoken narration (2026-07-23 fix) — verified by frame extraction on a real mood-visual clip.
- [ ] Human review of a real generated still/video (AGENT mode) — pending.
