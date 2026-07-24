# 02_beat_extraction

**Type:** AGENT — see `CLAUDE.md` §2 and `ARCHITECTURE.md` §2.

## Purpose

Interprets one scene's prose into an ordered list of filmable visual beats. This is the **shot-division** stage of the director-driven flow (screenplay → scene extraction → *shot division*): a **beat here IS a shot** — the two words are used interchangeably in this pipeline, and the field/id vocabulary stays `beat`/`beat_id` because the whole downstream chain (`01_2_scene_scoring`, `07_2_narration_shot_mapping`, `08_timeline_builder`) keys on it. Decides what counts as one filmable unit vs. two, and flags text with no direct visual analog (interior narration) rather than inventing a visual for it.

Input is one scene of prose: either `01_manuscript_ingestion`'s marker-split scene, or — when the opt-in `screenplay_frontend` flag is on — a scene rendered by `02_2_scene_extraction`. Either way this stage's contract is unchanged.

## I/O

- Input: `inputs/<scene_id>.txt` (from 01), `run_config.yaml` (tone, pacing).
- Output: `outputs/beats.json` (schema: `shared/schemas/beats.schema.json`).

## Run / test instructions

Implemented, running against a local Ollama model (`config/agents.yaml`, default `llama3.2:3b`):

```
python -m pytest stages/02_beat_extraction/tests/ -v   # mocked agent responses, no Ollama needed

python stages/02_beat_extraction/src/run.py \
  stages/02_beat_extraction/inputs \
  stages/02_beat_extraction/outputs \
  <path-to-run_config.yaml>
```

`inputs/` must contain exactly one scene `.txt` file (Stage 01's per-scene output, copied in). `run.py` also exposes `main(input_dir, output_dir, run_config, agent_call=None)` — `agent_call` is injectable, which is how tests mock the LLM per the fixture rule (canned responses, never a live call in tests).

## Numeric pass criterion

100% of `beats.json` entries schema-valid; 0 beats with a missing or empty `visual_description`. Qualitative coverage (are the beats *good*) is reviewed by the human against the numeric structural criterion, per `CLAUDE.md` rule 6.

**Result (2026-07-14, `llama3.2:3b` via Ollama, against `shared/fixtures/sample_scene.txt` / `ch1_sc1`): PASS.** 5 beats extracted (one per source paragraph), 0 with a missing/empty `visual_description`, 0 outside the mood vocabulary, 0 flagged `no_visual_analog`, schema-valid. 8/8 unit tests pass (mocked: complete path, invalid-JSON, bad-mood-tag, majority-no-visual-analog, missing-input, backend-error, plus prompt-rendering/wrapper-stripping unit tests). Real run took ~2-4 minutes on this hardware (CPU inference, 2GB-VRAM GPU too small to help) — `config/agents.yaml`'s `timeout_s` is set to 480 accordingly.

## Review checklist

- [x] Every beat traces to a real `text_excerpt_ref` in the source scene — spot-checked all 5 beats against the fixture text, no invented content found.
- [x] `no_visual_analog` flags are used, not silently dropped or forced into a fabricated visual (none needed for this fixture — it's all physical action).
- [x] Mood tags drawn only from `config/audio_spec.yaml`'s `mood_vocabulary`.
- [ ] Human review of real `outputs/beats.json` — pending.
