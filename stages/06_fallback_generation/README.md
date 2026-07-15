# 06_fallback_generation

**Type:** HYBRID — see `CLAUDE.md` §2 and `ARCHITECTURE.md` §2.

## Purpose

Agent half: writes an image-generation prompt from a beat's `visual_description`, tone, and mood tags for any beat with no acceptable retrieved asset — never inventing plot detail beyond the beat data. Code half: calls the generation backend, applies Ken Burns zoompan to turn a still into video-length footage, composes any card/title layout needed.

## I/O

- Input: `inputs/beats.json` + `inputs/candidates.json` (Stage 04's output — only beats with `routing.route == "06_fallback_generation"` are this stage's concern).
- Output: `outputs/fallback_prompt.json` (agent half, schema: `shared/schemas/fallback_prompt.schema.json`) + `outputs/assets_manifest.json` entries with `origin: generated_fallback`.

## Run / test instructions

Implemented, using `shared/agents/` (Ollama, same as Stage 02) for the prompt-writing half and `shared/generation/` (local `diffusers`, `stabilityai/sd-turbo`) + `shared/media.ken_burns_zoompan()` for the render half:

```
python -m pytest stages/06_fallback_generation/tests/ -v   # mocked agent/image-generator/zoompan, no model calls

python stages/06_fallback_generation/src/run.py \
  stages/06_fallback_generation/inputs \
  stages/06_fallback_generation/outputs \
  <path-to-run_config.yaml>
```

`main(input_dir, output_dir, run_config, agent_call=None, image_generator=None, zoompan=None)` — all three externals are injectable for testing. Beats not routed here are silently skipped (0 output if none match — a valid `COMPLETE`, not an error).

## Numeric pass criterion

100% of routed beats produce a usable asset — no silent drops (`CLAUDE.md` rule 7: "no match" is a routed outcome, not a crash). Generated asset duration matches the beat's `est_duration_s` within `config/thresholds.yaml` tolerance.

**Result (2026-07-14): PASS.** The real pipeline scene never routes anything here (all 5 beats found usable footage in Stage 05), so this was exercised against a synthetic fixture beat (`stages/06_fallback_generation/inputs/`, not part of the real run) deliberately chosen to be hard to source as stock footage: *"a translucent, ghostly figure of a woman stands at the top of the attic stairs, slowly fading into mist."* Ollama (`llama3.2:3b`) wrote a well-grounded prompt (style modifiers and negative-prompt defaults applied verbatim from `config/visual_style.yaml`, no invented detail beyond the beat text); `sd-turbo` rendered a genuinely strong, on-tone still in ~35s; `ken_burns_zoompan` produced a 4.0s clip matching the beat's `est_duration_s` exactly (ffprobe-verified). 7/7 unit tests pass (no-beats-routed no-op, full generate path, invalid-JSON and prompt-count-mismatch `NEEDS_INPUT`, unsafe-keyword-screen `NEEDS_INPUT`, render-failure `FAILED`, missing-input `FAILED`).

Bug found and fixed along the way: the agent sometimes emits `"scene_id": null` explicitly rather than omitting the key, and `dict.setdefault()` doesn't touch a key that's already present-but-null — `outputs/assets_manifest.json` failed schema validation on the first real run. Fixed by force-overwriting `run_id`/`scene_id` from context instead of `setdefault()` (same latent bug fixed in Stage 02 too).

## Review checklist

- [x] Generation prompts don't invent plot detail beyond what's in the beat (spot-checked the real run's `fallback_prompt.json`).
- [x] Generated visuals are plausible for the run's `tone` (read from `run_config`, never inferred from beat text — CLAUDE.md rule 11 / genre-agnostic policy).
- [x] `origin: generated_fallback` is tagged distinctly for QA (no attribution required, but flagged).
- [ ] Human review of the real generated still/video — pending.
