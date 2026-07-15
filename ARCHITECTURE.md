# ARCHITECTURE.md — Living Architecture, Gates, and Change Log

This document is the detailed architectural companion to `CLAUDE.md`. `CLAUDE.md` sets the non-negotiable rules; this file describes *how* the system is built to satisfy them, and is expected to change over time (see Change Log at the bottom — every change here is logged, per Rule 9).

---

## 1. System overview

The pipeline turns one chapter/scene of prose into a rendered video cut. It is a strict linear sequence of 13 stages, each independently runnable and testable, coordinated hub-and-spoke by Stage 00. No stage talks to another stage directly.

```
 manuscript text
       │
       ▼
[01 manuscript_ingestion]  CODE
       │  scenes/chapters split by explicit markers
       ▼
[02 beat_extraction]       AGENT
       │  beats.json — ordered list of filmable visual beats
       ▼
[03 candidate_fetch]       CODE
       │  candidates.json — raw hits per beat from FootageSource APIs
       ▼
[04 clip_reranking]        CODE
       │  CLIP-embedding cosine score, threshold routing
       ▼
       ├─ above threshold ──────────────┐
       │                                ▼
       │                    [05 retrieval_verification]  CODE + HITL
       │                          frame-sample re-score, human tie-break
       │                                │
       └─ below threshold ──────┐       │
                                 ▼       ▼
                      [06 fallback_generation]  HYBRID
                         image-gen prompt (agent) + render (code)
                                 │       │
                                 └───┬───┘
                                     ▼
                        assets_manifest.json (winning asset per beat,
                        license + attribution captured at fetch time)
                                     │
                                     ▼
                     [07 editorial_direction]  AGENT
                        edit_plan.json — shot division, pacing, transitions
                                     │
                                     ▼
                     [08 timeline_builder]  CODE
                        timeline.json — materialized, validated
                                     │
                                     ▼
                     [09 audio_production]  HYBRID
                        audio_plan.json (agent) → audio_mix.json + stems (code)
                                     │
                                     ▼
                     [10 human_review_gate]  CODE + human
                        contact-sheet HTML, human sign-off
                                     │
                                     ▼
                     [11 assembly_render]  CODE
                        final.mp4 (ffmpeg, fully deterministic)
                                     │
                                     ▼
                     [12 qa_attribution]  CODE
                        schema validation, license/attribution completeness,
                        duration + loudness checks, CREDITS.md
```

All arrows are Coordinator-mediated Task Envelopes / Stage Responses (see `SCHEMAS.md`), never direct stage-to-stage calls. `00_coordinator` sits beside this chain, not "before" it — it invokes every stage, holds run-level state, and is the only component that talks to the human.

---

## 2. Per-stage detail

Stages 07 and 09 are already specified in full in `CLAUDE.md` §4 — not repeated here. The remaining stages:

**00_coordinator (AGENT orchestration + CODE core).** The code core owns: envelope construction, stage invocation order, schema validation of every Stage Response against `expected_output_schema`, gate enforcement (refuses Stage N+1 without `outputs/APPROVED.md` on Stage N), and append-only logging to `shared/runs/<run_id>/coordinator_log.jsonl`. The agent layer owns: turning a `NEEDS_INPUT` or `ESCALATE` Stage Response into a human-readable question with options and tradeoffs, and batching multiple pending questions from one stage into a single presentation. The agent never decides sequencing or gate pass/fail — that's code, checked against `APPROVED.md` presence, not agent judgment.

**01_manuscript_ingestion (CODE).** Input: raw manuscript text file + an explicit marker convention (e.g. `## Chapter` / `### Scene` headers, configurable, not inferred) declared in `run_config.yaml`. Output: one or more scene text files, encoding-normalized (UTF-8), with scene boundaries and any inline metadata (POV character, chapter number) preserved as plain fields. No interpretation of content — pure text splitting.

**02_beat_extraction (AGENT).** Input: one scene's text + `run_config` (tone, pacing). Output: `beats.json`, an ordered list of filmable visual beats, each with a text excerpt reference, a plain-language visual description (what a camera would see), estimated on-screen duration, and mood/intensity tags drawn from `config/audio_spec.yaml`'s vocabulary. This is the first and most judgment-heavy step: deciding what counts as one filmable unit versus two, and what's genuinely visual versus interior narration that has no direct visual analog (those get flagged, not invented).

**03_candidate_fetch (CODE).** Input: `beats.json`. For each beat, queries every enabled `FootageSource` (see `shared/sources/`) with search terms derived mechanically from the beat's visual description (no agent involved in query construction — that's a deterministic keyword/phrase extraction). Output: `candidates.json`, raw API hits per beat, each candidate tagged with source, license, and a stub for the not-yet-computed similarity score. Caching and rate limiting live here.

**04_clip_reranking (CODE).** Input: `candidates.json`. Computes CLIP embedding similarity between each candidate's thumbnail/keyframe and the beat's visual description, via `shared/embeddings/`. Routes each beat: above `thresholds.yaml`'s similarity cutoff → `05_retrieval_verification`; below → `06_fallback_generation`; within the configured margin of the cutoff → flagged `retrievable: low` for human tie-break in 05.

**05_retrieval_verification (CODE + HITL).** Downloads the top-k candidates per beat, samples frames across the actual clip (not just the thumbnail), re-scores. Beats whose top candidates are within the "close" margin from `thresholds.yaml` are batched and presented to the human via the Coordinator (Rule 10) rather than auto-selected. Output: winning asset per beat with confidence, or a `FALLBACK_ROUTED` handoff to 06 for beats that fail verification.

**06_fallback_generation (HYBRID).** Agent half: for beats with no acceptable retrieved asset, writes an image-generation prompt from the beat's visual description, tone, and mood tags — never inventing plot detail beyond what's in the beat. Code half: calls the generation backend, applies Ken Burns zoompan to turn a still into video-length footage, composes any card/title layout needed. Output feeds into the same `assets_manifest.json` as 05's winners, tagged with source `generated` (no attribution needed, but flagged distinctly for QA).

**08_timeline_builder (CODE).** Input: approved `edit_plan.json` + `assets_manifest.json`. Pure transformation: resolves every shot's in/out offsets and transition into a single `timeline.json` (absolute timecodes, file references, transition parameters), and validates it against `shared/schemas/timeline.schema.json`. No creative decisions — those were already made and approved in 07.

**10_human_review_gate (CODE + human).** Generates a contact-sheet HTML (thumbnail grid + timeline scrub markers + audio cue markers) from `timeline.json` and `audio_mix.json`. The human reviews and either approves (writes `outputs/APPROVED.md`) or requests changes, which route back through the Coordinator to the relevant upstream stage. No agent involvement — this stage's only job is rendering a reviewable artifact.

**11_assembly_render (CODE).** Input: approved `timeline.json` + `audio_mix.json`. Deterministic ffmpeg pipeline: trim, concat, color grade (if specified), mux audio, output `final.mp4`. No decisions made here that weren't already fixed upstream.

**12_qa_attribution (CODE).** Validates every artifact against its schema, checks every fetched asset in the run manifest has a complete license/attribution record (CC-BY assets without a creator record fail QA), checks final duration against the beat plan's target (within `thresholds.yaml`'s tolerance) and loudness against `audio_spec.yaml`'s LUFS target. Emits `CREDITS.md` and a QA report. A failing QA report blocks the run from being marked done — it does not block re-running upstream stages.

---

## 3. Gate 0 — manual coverage test (precedes ALL implementation)

**Purpose.** Before writing a single line of pipeline code, prove that the Source Policy (CLAUDE.md §0 — Pexels, Pixabay, Mixkit, Coverr, Archive.org public domain, Wikimedia Commons, NASA) can plausibly cover a real scene's visual beats at a workable rate. If it can't, the whole retrieval-based design (stages 03-05) needs to pivot toward heavier reliance on the fallback-generation lane before any of that code gets built. This is a coverage feasibility test, not a pipeline dry run — no code is exercised.

**Procedure.**
1. Take the standing fixture scene at `shared/fixtures/sample_scene.txt` (or a real manuscript excerpt, once available — the procedure is identical either way).
2. By hand, decompose it into 8-12 visual beats — exactly the judgment call Stage 02's agent will later automate. Write each beat as a one-sentence "what would the camera see" description.
3. For each beat, manually search Pexels, Pixabay, Mixkit, Archive.org (public domain filter on), and Wikimedia Commons via their web UIs (no API keys required for this manual pass). Try 1-3 search-term variations per beat.
4. For each beat, record: search terms tried, best candidate found (title/URL/source), its license, and a relevance judgment — `good` (would plausibly be selected by a real similarity threshold), `marginal` (fallback-eligible but not a clean match), or `none` (nothing usable found).
5. Compute coverage: `(good + marginal) / total beats`, and separately, `good / total beats`.
6. Record everything in `docs/GATE0_RESULTS.md` and make a GO/PIVOT call against the criterion below.

**Numeric pass criterion** (seeded in `config/thresholds.yaml` as `gate0_min_coverage_pct: 70`):
- GO if `(good + marginal) / total beats >= 70%` **and** zero beats are `none` (every beat has at least a fallback-eligible candidate, since `none` beats reveal a gap not just in stock coverage but in the fallback-generation lane's assumptions too).
- Otherwise PIVOT: stop, do not proceed to stage implementation, escalate to the human with the specific failing beats and a recommendation (e.g. lean harder on fallback-generation, reconsider genre/scene selection for early testing, or add a source).
- A criterion failing twice after adjustments escalates per CLAUDE.md Rule 6 — never silently lowered.

**Status:** This session, only the procedure and the `docs/GATE0_RESULTS.md` template are being created. Running Gate 0 to an actual GO/PIVOT decision is a follow-up step requiring a live manual search pass — see that file.

---

## 4. Change Log

| Date | What | Why | Approved by |
|---|---|---|---|
| 2026-07-14 | Initial creation of ARCHITECTURE.md, SCHEMAS.md, LICENSES.md, DECISIONS_LOG.md, and full repo scaffold (config/, shared/, stages/) | Bootstrap the project structure CLAUDE.md v2 already assumes exists; nothing existed yet beyond CLAUDE.md itself | rpprabha1@gmail.com |
| 2026-07-14 | Gate 0 executed against `shared/fixtures/sample_scene.txt`: 100% beat coverage (good+marginal), 0 dead-end beats — GO. See `docs/GATE0_RESULTS.md`. Rule 5 now unblocked; Stage 01 implementation starting. | Required before any pipeline code per CLAUDE.md rule 5 | rpprabha1@gmail.com |
| 2026-07-14 | Added `scenes_manifest.schema.json` + SCHEMAS.md entry for 01_manuscript_ingestion's output; this contract wasn't anticipated in the initial schema set | Discovered while implementing Stage 01 — 01's output needed a formal contract before 02 can consume it | rpprabha1@gmail.com |
| 2026-07-14 | Added `shared/agents/` (Ollama backend + config loader) and `config/agents.yaml`. AGENT/HYBRID stages (02, 06, 07, 09) run against a local Ollama model (default `llama3.2:3b`, sized to the dev machine's 2GB-VRAM GPU / ~8GB RAM) rather than a hosted LLM API. | Human decision: use local open-source models via Ollama, sized appropriately to available hardware, instead of a paid hosted API | rpprabha1@gmail.com |
| 2026-07-14 | Implemented `shared/sources/pexels.py` + `pixabay.py` (the two `FootageSource` adapters with API keys in progress) and added `shared/manifest.py`, a small helper every fetching stage (03, and later 05/06/09) uses to append/de-dupe `shared/runs/<run_id>/manifest.json` entries — this cross-stage helper wasn't anticipated in the initial scaffold. | Discovered while implementing Stage 03 — manifest-append logic is identical across every fetching stage and belongs in `shared/`, not duplicated per stage | rpprabha1@gmail.com |
| 2026-07-14 | Implemented `shared/embeddings/` (HuggingFace `transformers` CLIP, `openai/clip-vit-base-patch32`, config in new `config/embeddings.yaml`). Extended `candidates.schema.json` with an optional per-beat `routing` object (`route`/`best_score`/`retrievable`) written by Stage 04; also widened `similarity_score`'s range to `[-1, 1]` (cosine similarity can be negative; the original `[0, 1]` bound was wrong). | Discovered while implementing Stage 04 — routing decision needed a schema home, and the similarity_score bound was an error in the initial schema pass | rpprabha1@gmail.com |
| 2026-07-14 | Added optional `download_url` to `candidates.schema.json` and to `FootageCandidate`/`PexelsSource`/`PixabaySource`. Stage 03 had only captured each candidate's human-facing page URL, not a direct downloadable file URL — Stage 05 needs the latter to actually download video for frame-sampling verification. | Discovered while starting Stage 05 — a real gap in Stage 03/04's output, not anticipated when those stages were built | rpprabha1@gmail.com |
| 2026-07-14 | Added `shared/media/` (ffmpeg/ffprobe CLI wrappers: `probe_duration_s`, `extract_frames`). Used by Stage 05 for frame sampling now; will be reused by Stage 06's Ken Burns zoompan and Stage 11's assembly render rather than duplicating ffmpeg subprocess calls per stage. | Anticipated reuse across every stage that touches actual video files | rpprabha1@gmail.com |
| 2026-07-14 | Added `shared/generation/` (HuggingFace `diffusers`, `stabilityai/sd-turbo`, config in new `config/image_gen.yaml`) for Stage 06's code half, `shared/media.ken_burns_zoompan()` for turning a still into video-length footage, `config/visual_style.yaml` (tone -> image style modifiers + negative-prompt defaults, same pattern as `audio_spec.yaml`'s tone->music-tag map), and `fallback_prompt.schema.json` for the agent-half output. Human decision: local diffusion on CPU (sd-turbo), consistent with the Ollama/CLIP local-model choices already made — smoke-tested at ~35-60s/image, quality judged good enough for the fallback lane. | Human decision, matching the established "local models sized to this hardware" pattern from `config/agents.yaml`/`config/embeddings.yaml` | rpprabha1@gmail.com |
| 2026-07-14 | Fixed `edit_plan.schema.json`: `shots` had `maxItems: 3`, which made CLAUDE.md's own stated behavior (">3 shots is a HITL trigger") schema-invalid to represent. Raised to `maxItems: 12` (a sanity ceiling only); the >3 HITL check is enforced in Stage 07's code, not the schema. | Caught before Stage 07 implementation began — a real authoring error in the initial schema pass | rpprabha1@gmail.com |
| 2026-07-14 | Added `config/thresholds.yaml`'s `editorial.hold_duration_clamp_tolerance_pct` (10%): a `hold_duration_s` within this tolerance of the active pacing preset's range is coerced to the nearest bound in Stage 07's code instead of blocking on `NEEDS_INPUT`. The range itself is unchanged — this is coercion of a near-miss, not a lowered threshold. | Human decision after the real run showed `llama3.2:3b` has a small, systematic numeric-precision limitation on hold_duration_s (not a prompt-wording issue); see `DECISIONS_LOG.md` | rpprabha1@gmail.com |
| 2026-07-14 | Clarified `edit_plan.schema.json`'s shot field semantics: `[in_s, out_s]` is the usable source window (an availability ceiling), `hold_duration_s` is the authoritative on-screen duration and must fit inside that window (`hold_duration_s <= out_s - in_s`) — `08_timeline_builder` trims to `[in_s, in_s + hold_duration_s]`, never to `out_s` directly. Added a Stage 07 code-side check enforcing this, since the real edit_plan.json had `out_s` values looser than `hold_duration_s` and the schema didn't previously say which was authoritative. | Caught while starting Stage 08 — the ambiguity would have forced 08 to guess | rpprabha1@gmail.com |
| 2026-07-14 | Corrected `LICENSES.md`: Pixabay Music was listed as having a public API ("music endpoint") — this was never actually verified and is false. Pixabay's documented public API covers images and videos only. | Caught while starting Stage 09 — would have built a `MusicSource` adapter against an API that doesn't exist | rpprabha1@gmail.com |
| 2026-07-14 | Added `shared/sources/music_base.py` (`MusicSource`/`MusicCandidate`, mirrors `FootageSource`) and `shared/sources/manual_music.py` (`ManualMusicSource`, a human-curated candidate list — the only real `MusicSource` implementation until an approved source gets a public API). Added `shared/generation/tts_backend.py` (local `piper-tts`, `config/tts.yaml`, model in `shared/models/piper/`) and `shared/media/audio_mix.py` (crossfade, ducking, narration overlay, loudness normalization — smoke-tested against synthetic sine-wave audio, measured LUFS landed within 0.11 of target). Added `music_cue_intent.schema.json` for the agent-half output. | Human decisions: local `piper-tts` for narration (same "fit this hardware" pattern as Ollama/CLIP/sd-turbo); manual music sourcing given no approved source has a real search API | rpprabha1@gmail.com |
| 2026-07-15 | Fixed `shared/media/audio_mix.py`'s `normalize_loudness()`: single-pass `loudnorm` measured 1.44 LU off the -16.0 target on a real mix (outside `audio_spec.yaml`'s `tolerance_lu: 1.0` — a genuine numeric-criterion failure, not a rounding note). Switched to standard two-pass loudnorm (analysis pass measures real input stats, apply pass uses `linear=true` with those measured values); re-verified against synthetic audio at 0.05 LU off target. | Rule 6: a failing numeric criterion doesn't get waved through | rpprabha1@gmail.com |
| 2026-07-15 | **Architecture change: audio timing now drives video timing, not the reverse.** Real run exposed that Stage 07's visual `hold_duration_s` (2.75-3.75s/beat, picked with zero awareness of narration) was wildly shorter than the actual time to read each beat's full source paragraph aloud (8-15s/beat) — narration stems placed at visual beat-start times overlapped 3-5 deep into an unusable mix. Fixed: `09_audio_production` no longer reads `edit_plan.json` at all (only `beats.json`'s order + the scene's source text); narration stems are now placed sequentially by actual synthesized duration, and the resulting (longer) sum becomes `audio_mix.json`'s new `total_duration_s` field (schema updated). **Consequence, not yet implemented:** `08_timeline_builder`'s `timeline.json` is now stale after 09 runs — each beat's visual hold must be regenerated to at least match its narration duration before `11_assembly_render` can use it. This is an open follow-up, not silently papered over. | Human decision: narration timing must be authoritative for a narrated-prose format; visual cuts follow audio, not the other way around | rpprabha1@gmail.com |
