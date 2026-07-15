# SCHEMAS.md — Inter-stage Data Contracts

Every artifact that crosses a stage boundary validates against a JSON Schema in `shared/schemas/`. The Coordinator validates every Stage Response's output against `expected_output_schema` before accepting it (CLAUDE.md §5). This document lists each contract with a realistic sample. Schema changes update this file and the backing `.schema.json` in the same commit, plus a Change Log entry in `ARCHITECTURE.md` (Rule 9).

---

## Task Envelope
Schema: `shared/schemas/task_envelope.schema.json`. Coordinator → stage.

```json
{
  "envelope_id": "b3f1c2a0-1234-4abc-9def-000000000001",
  "run_id": "run_2026_07_ch1",
  "stage": "07_editorial_direction",
  "attempt": 1,
  "input_manifest": ["inputs/beats.json", "inputs/assets_manifest.json"],
  "run_config_ref": "shared/runs/run_2026_07_ch1/run_config.yaml",
  "expected_output_schema": "shared/schemas/edit_plan.schema.json",
  "deadline_hint_s": 600
}
```

## Stage Response
Schema: `shared/schemas/stage_response.schema.json`. Stage → Coordinator, exactly one of four `status` values.

```json
{
  "envelope_id": "b3f1c2a0-1234-4abc-9def-000000000001",
  "run_id": "run_2026_07_ch1",
  "stage": "07_editorial_direction",
  "status": "NEEDS_INPUT",
  "needs_input": [
    {
      "reason_code": "asset_too_short",
      "question": "Beat b004's winning asset is 1.8s but the minimum viable shot length is 2.0s. How should this be handled?",
      "options": ["Re-route beat to fallback generation", "Accept with a static hold extension", "Manually source a replacement asset"],
      "context_ref": "inputs/assets_manifest.json#b004"
    }
  ]
}
```

## Run Config
Schema: `shared/schemas/run_config.schema.json`. Lives at `shared/runs/<run_id>/run_config.yaml`; carries ALL story/tone-specific intent so no stage code branches on genre (CLAUDE.md §0 hard rule).

```json
{
  "run_id": "run_2026_07_ch1",
  "manuscript_ref": "shared/runs/run_2026_07_ch1/manuscript.txt",
  "tone": "gothic-suspense",
  "pacing": "slow-burn",
  "music_intensity_curve": "rising",
  "scene_marker_convention": { "chapter_marker": "## Chapter", "scene_marker": "### Scene" },
  "pov_character": "Elena"
}
```

## Scenes Manifest
Schema: `shared/schemas/scenes_manifest.schema.json`. Output of `01_manuscript_ingestion`.

```json
{
  "run_id": "run_2026_07_ch1",
  "manuscript_ref": "shared/runs/run_2026_07_ch1/manuscript.txt",
  "scenes": [
    {
      "scene_id": "ch1_sc1",
      "order": 0,
      "chapter_number": 1,
      "scene_number_in_chapter": 1,
      "heading_text": "## Chapter 1\n### Scene 1",
      "file_ref": "outputs/ch1_sc1.txt",
      "pov_character": "Elena"
    }
  ]
}
```

## Beats
Schema: `shared/schemas/beats.schema.json`. Output of `02_beat_extraction`.

```json
{
  "run_id": "run_2026_07_ch1",
  "scene_id": "ch1_sc1",
  "beats": [
    {
      "beat_id": "b001",
      "order": 0,
      "text_excerpt_ref": "para:1-2",
      "visual_description": "A narrow attic staircase, dust motes lit by a single shaft of light from a high window",
      "est_duration_s": 3.5,
      "mood_tags": ["tense", "quiet"],
      "no_visual_analog": false
    }
  ]
}
```

## Candidates
Schema: `shared/schemas/candidates.schema.json`. Output of `03_candidate_fetch`; `similarity_score` is added in-place by `04_clip_reranking`.

```json
{
  "run_id": "run_2026_07_ch1",
  "scene_id": "ch1_sc1",
  "candidates_by_beat": [
    {
      "beat_id": "b001",
      "search_terms": ["attic staircase dust light", "old wooden stairs sunbeam"],
      "candidates": [
        {
          "candidate_id": "pexels_3021",
          "source": "pexels",
          "url": "https://www.pexels.com/video/example-3021",
          "license": "Pexels License",
          "thumbnail_ref": "cache/pexels_3021_thumb.jpg",
          "download_url": "https://videos.pexels.com/video-files/3021/3021_540x960.mp4",
          "duration_s": 12.0,
          "similarity_score": 0.81
        }
      ],
      "routing": { "route": "05_retrieval_verification", "best_score": 0.81, "retrievable": "high" }
    }
  ]
}
```

## Assets Manifest
Schema: `shared/schemas/assets_manifest.schema.json`. Winning asset per beat, from `05_retrieval_verification` or `06_fallback_generation`.

```json
{
  "run_id": "run_2026_07_ch1",
  "scene_id": "ch1_sc1",
  "assets": [
    {
      "beat_id": "b001",
      "asset_id": "pexels_3021",
      "origin": "retrieved_verified",
      "file_ref": "shared/runs/run_2026_07_ch1/assets/pexels_3021.mp4",
      "duration_s": 12.0,
      "confidence": 0.81,
      "license": "Pexels License",
      "attribution": { "source": "pexels", "creator_required": false }
    }
  ]
}
```

## Fallback Prompt
Schema: `shared/schemas/fallback_prompt.schema.json`. Agent half of `06_fallback_generation`.

```json
{
  "run_id": "run_2026_07_ch1",
  "scene_id": "ch1_sc1",
  "prompts": [
    {
      "beat_id": "ch1_sc1_b003",
      "image_prompt": "a woman kneeling beside an open antique trunk, holding a stack of old photographs and a browned letter, tense atmosphere, still composition, moody, desaturated, high contrast shadows, cinematic",
      "negative_prompt": "text, watermark, logo, blurry, extra limbs, distorted anatomy, low quality",
      "rationale": "Grounded directly in the beat's kneeling/trunk/photographs/letter action; mood_tags quiet+tense translated to still composition and tense atmosphere."
    }
  ]
}
```

## Edit Plan
Schema: `shared/schemas/edit_plan.schema.json`. Output of `07_editorial_direction` (full field description in CLAUDE.md §4).

```json
{
  "run_id": "run_2026_07_ch1",
  "scene_id": "ch1_sc1",
  "total_runtime_s": 3.5,
  "beats": [
    {
      "beat_id": "b001",
      "asset_id": "pexels_3021",
      "shots": [
        { "shot_id": "b001_s1", "in_s": 0.0, "out_s": 3.5, "hold_duration_s": 3.5 }
      ],
      "transition_out": "hard-cut",
      "rationale": "Default cut; no dramatic emphasis needed for establishing beat"
    }
  ]
}
```

## Music Cue Intent
Schema: `shared/schemas/music_cue_intent.schema.json`. Agent half of `09_audio_production`, before the code half searches for and shortlists real tracks (merged into `audio_plan.json` below).

```json
{
  "run_id": "run_2026_07_ch1",
  "scene_id": "ch1_sc1",
  "cues": [
    {
      "cue_id": "cue001",
      "start_beat_id": "ch1_sc1_b001",
      "end_beat_id": "ch1_sc1_b005",
      "mood_tags": ["tense", "quiet"],
      "target_intensity": 0.35,
      "rationale": "The scene is a single sustained quiet-dread arc - no mood shift sharp enough to justify a second cue."
    }
  ]
}
```

## Audio Plan
Schema: `shared/schemas/audio_plan.schema.json`. Agent half of `09_audio_production` (full field description in CLAUDE.md §4).

```json
{
  "run_id": "run_2026_07_ch1",
  "scene_id": "ch1_sc1",
  "cues": [
    {
      "cue_id": "cue001",
      "start_beat_id": "b001",
      "end_beat_id": "b004",
      "mood_tags": ["tense", "quiet"],
      "target_intensity": 0.3,
      "candidate_shortlist": [
        { "track_ref": "pixabay_music_5511", "source": "pixabay_music", "license": "Pixabay License" },
        { "track_ref": "mixkit_2201", "source": "mixkit", "license": "Mixkit License" }
      ],
      "rationale": "Low-intensity sustained pad to build dread without overtaking narration"
    }
  ]
}
```

## Audio Mix
Schema: `shared/schemas/audio_mix.schema.json`. Code half of `09_audio_production`.

```json
{
  "run_id": "run_2026_07_ch1",
  "scene_id": "ch1_sc1",
  "narration_stems": [
    { "beat_id": "b001", "file_ref": "shared/runs/run_2026_07_ch1/audio/narr_b001.wav", "start_s": 0.0, "duration_s": 3.2 }
  ],
  "music_stems": [
    { "cue_id": "cue001", "track_ref": "pixabay_music_5511", "file_ref": "shared/runs/run_2026_07_ch1/audio/cue001.mp3", "start_s": 0.0, "duration_s": 14.0, "selected_by": "rpprabha1@gmail.com", "crossfade_in_s": 0.0, "crossfade_out_s": 1.5 }
  ],
  "mix_params": { "ducking_depth_db": -12, "ducking_attack_ms": 150 },
  "final_lufs": -16.0
}
```

## Timeline
Schema: `shared/schemas/timeline.schema.json`. Output of `08_timeline_builder`.

```json
{
  "run_id": "run_2026_07_ch1",
  "scene_id": "ch1_sc1",
  "clips": [
    {
      "shot_id": "b001_s1",
      "file_ref": "shared/runs/run_2026_07_ch1/assets/pexels_3021.mp4",
      "source_in_s": 0.0,
      "source_out_s": 3.5,
      "timeline_start_s": 0.0,
      "timeline_end_s": 3.5,
      "transition_out": { "type": "hard-cut", "duration_s": 0.0 }
    }
  ],
  "total_duration_s": 3.5
}
```

## Run Manifest
Schema: `shared/schemas/manifest.schema.json`. Appended by every fetching stage; the source of `CREDITS.md`.

```json
{
  "run_id": "run_2026_07_ch1",
  "entries": [
    {
      "entry_id": "pexels_3021",
      "kind": "footage",
      "fetched_by_stage": "03_candidate_fetch",
      "fetched_at": "2026-07-14T10:03:00Z",
      "source": "pexels",
      "source_url": "https://www.pexels.com/video/example-3021",
      "creator": "Jane Doe",
      "license": "Pexels License",
      "attribution_required": false
    }
  ]
}
```

## QA Report
Schema: `shared/schemas/qa_report.schema.json`. Output of `12_qa_attribution`.

```json
{
  "run_id": "run_2026_07_ch1",
  "scene_id": "ch1_sc1",
  "checks": [
    { "name": "schema_validation", "pass": true, "detail": "All artifacts validate" },
    { "name": "attribution_completeness", "pass": true, "detail": "All CC-BY assets have creator records" },
    { "name": "duration_tolerance", "pass": true, "detail": "Final 41.2s vs target 40.0s (3% drift, within tolerance)" },
    { "name": "loudness_spec", "pass": true, "detail": "-16.0 LUFS matches config/audio_spec.yaml target" }
  ],
  "pass": true
}
```
