# 09_audio_production

**Type:** HYBRID — fully specified in `CLAUDE.md` §4 and `ARCHITECTURE.md` §2 (not repeated here, that's the source of truth).

## Purpose

Agent half produces `audio_plan.json` (music cue sheet with mood tags, cue boundaries, target intensity, and a 2-3 track shortlist per cue). Code half does TTS synthesis, restricted `MusicSource` search, mixing (ducking/crossfades/normalization) per `config/audio_spec.yaml`, emitting `audio_mix.json` + rendered stems. Final track selection is always a mandatory HITL decision — the agent never picks alone.

## I/O

- Input: `inputs/beats.json` (order + `text_excerpt_ref` + mood tags), `inputs/scene_text.txt` (the scene's source prose), `run_config.yaml`. **Does not read `edit_plan.json`** — see the architecture change below.
- Output: `outputs/music_cue_intent.json` (agent half, schema: `shared/schemas/music_cue_intent.schema.json`), `outputs/audio_mix.json` (schema: `shared/schemas/audio_mix.schema.json`), `outputs/scene_mix.wav` (the actual rendered mix) + cached stems under `shared/runs/<run_id>/cache/{narration,music,mix}/`.

## Run / test instructions

Implemented, using `shared/agents/` (Ollama, cue sheet), `shared/generation/` (local `piper-tts`, narration), `shared/sources/` (`ManualMusicSource` — no approved music source has a real search API, see `LICENSES.md`), and `shared/media/audio_mix.py` (crossfade, ducking, overlay, two-pass loudness normalization):

```
python -m pytest stages/09_audio_production/tests/ -v   # mocked agent/music-source/TTS, real ffmpeg against synthetic sine-wave fixtures

python stages/09_audio_production/src/run.py \
  stages/09_audio_production/inputs \
  stages/09_audio_production/outputs \
  <path-to-run_config.yaml>
```

`main(...)` takes `agent_call`, `music_source`, `tts_fn`, `downloader`, `audio_spec`, `hitl_decisions`, `selected_by` — all injectable. `hitl_decisions` (`{cue_id: chosen_track_ref}`) resolves a prior `NEEDS_INPUT` track-selection response on a second call, same pattern as Stage 05.

**Architecture change (2026-07-15): audio timing drives video timing, not the reverse.** Narration is the full source paragraph per beat, read verbatim (human decision) — that routinely takes 8-15s, far longer than Stage 07's visual `hold_duration_s` (2.75-3.75s), which was picked with zero awareness of narration length. Narration stems are placed **sequentially by actual synthesized duration**, never at the visual beat-start times, and the resulting (longer) sum is `audio_mix.json`'s `total_duration_s`. **Open follow-up, not yet done:** `08_timeline_builder`'s `timeline.json` is now stale relative to this — each beat's visual hold needs to be regenerated to at least match its narration duration before `11_assembly_render` can use it.

## Numeric pass criterion

`final_lufs` within `config/audio_spec.yaml`'s `loudness.tolerance_lu` of `target_lufs`; every cue has a track selected with a corresponding `DECISIONS_LOG.md` entry; narration stems never overlap.

**Result (2026-07-15, against the real scene's 5 beats + 3 real Mixkit candidates): PASS, after fixing two real bugs found on the way.** `final_lufs=-15.97` (target -16.0, well within the ±1.0 tolerance) — single-pass `loudnorm` had originally measured 1.44 LU off target (a genuine criterion failure); switched to standard two-pass loudnorm. Narration stems are correctly sequential (0.0s, 14.70s, 27.88s, 42.75s, 55.82s — each starting exactly where the previous ends), `scene_mix.wav` runs the full 63.1s the narration actually needs. Also fixed: `overlay_narration`'s `amix` used `duration=first`, which silently truncated the whole mix to the music track's length when a stale cached (pre-fix, short) music file wasn't cleared between runs — switched to `duration=longest` so this can't silently truncate again regardless of caching. The agent reliably left beat 1 uncovered by its single cue (3/3 reproductions, reasoning the mood "shifts" at beat 2) — code auto-repairs this specific safe pattern (single cue, only a leading-beat gap) rather than blocking every run on it. 9/9 unit tests pass.

## Review checklist

- [x] Mood tags drawn only from `config/audio_spec.yaml`'s vocabulary (code-checked, `NEEDS_INPUT` on violation).
- [x] Attribution-requiring tracks are flagged into the manifest at fetch time (Mixkit requires none per `LICENSES.md`; `requires_attribution` flows through regardless for a future source that does).
- [x] Ducking/crossfade/normalization math reads `audio_spec.yaml`, never hardcodes values.
- [x] Narration stems never overlap — verified both by unit test and by inspecting the real run's timestamps.
