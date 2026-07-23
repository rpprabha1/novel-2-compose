from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

# See stage 01's test_run.py for why this isn't a plain "import run".
STAGE_SRC = Path(__file__).resolve().parents[1] / "src"
_spec = importlib.util.spec_from_file_location("stage06_fallback_generation_run", STAGE_SRC / "run.py")
run = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = run
_spec.loader.exec_module(run)

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))
from shared.media import FFmpegError  # noqa: E402

RUN_CONFIG = {"run_id": "test_run_06", "tone": "gothic-suspense"}

VALID_PROMPTS_JSON = json.dumps(
    {
        "prompts": [
            {
                "beat_id": "b1",
                "image_prompt": "a woman kneeling beside an open trunk, tense atmosphere, moody, cinematic",
                "negative_prompt": "text, watermark, logo, blurry, extra limbs, distorted anatomy, low quality",
                "rationale": "grounded in beat text",
            }
        ]
    }
)


def _make_inputs(routes: dict[str, str]) -> tuple[dict, dict]:
    beats = {
        "run_id": "test_run_06",
        "scene_id": "ch1_sc1",
        "beats": [
            {
                "beat_id": beat_id,
                "order": i,
                "text_excerpt_ref": f"para:{i + 1}",
                "visual_description": "a woman kneeling beside an open trunk",
                "est_duration_s": 4.0,
                "mood_tags": ["quiet", "tense"],
                "no_visual_analog": False,
            }
            for i, beat_id in enumerate(routes)
        ],
    }
    candidates = {
        "run_id": "test_run_06",
        "scene_id": "ch1_sc1",
        "candidates_by_beat": [
            {
                "beat_id": beat_id,
                "search_terms": ["desc"],
                "candidates": [],
                "routing": {"route": route, "best_score": -1.0, "retrievable": "none"},
            }
            for beat_id, route in routes.items()
        ],
    }
    return beats, candidates


def _write(input_dir: Path, beats: dict, candidates: dict) -> None:
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "beats.json").write_text(json.dumps(beats), encoding="utf-8")
    (input_dir / "candidates.json").write_text(json.dumps(candidates), encoding="utf-8")


def _clean_run_dir(run_id: str) -> None:
    run_dir = REPO_ROOT / "shared" / "runs" / run_id
    if run_dir.exists():
        shutil.rmtree(run_dir)


def _noop_image_generator(prompt: str, negative_prompt: str, dest_path: Path) -> None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(b"fake png")


def _noop_zoompan(image_path: Path, output_path: Path, duration_s: float) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"fake mp4")


def test_no_beats_routed_here_completes_noop(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    beats, candidates = _make_inputs({"b1": "05_retrieval_verification"})
    _write(input_dir, beats, candidates)

    response = run.main(input_dir, output_dir, RUN_CONFIG)

    assert response.status.value == "COMPLETE"
    assert not (output_dir / "assets_manifest.json").exists()


def test_complete_generates_assets(tmp_path):
    run_id = "test_run_06"
    _clean_run_dir(run_id)
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    beats, candidates = _make_inputs({"b1": "06_fallback_generation"})
    _write(input_dir, beats, candidates)

    response = run.main(
        input_dir, output_dir, RUN_CONFIG,
        agent_call=lambda s, u: VALID_PROMPTS_JSON,
        image_generator=_noop_image_generator,
        zoompan=_noop_zoompan,
    )

    assert response.status.value == "COMPLETE"
    out = json.loads((output_dir / "assets_manifest.json").read_text(encoding="utf-8"))
    assert out["assets"][0]["beat_id"] == "b1"
    assert out["assets"][0]["origin"] == "generated_fallback"

    manifest = json.loads((REPO_ROOT / "shared" / "runs" / run_id / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["entries"][0]["kind"] == "generated_image"

    _clean_run_dir(run_id)


def test_invalid_json_needs_input(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    beats, candidates = _make_inputs({"b1": "06_fallback_generation"})
    _write(input_dir, beats, candidates)

    response = run.main(input_dir, output_dir, RUN_CONFIG, agent_call=lambda s, u: "not json")

    assert response.status.value == "NEEDS_INPUT"
    assert response.needs_input[0].reason_code == "no_prompts_produced"


def test_mismatched_prompt_count_needs_input(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    beats, candidates = _make_inputs({"b1": "06_fallback_generation", "b2": "06_fallback_generation"})
    _write(input_dir, beats, candidates)

    response = run.main(input_dir, output_dir, RUN_CONFIG, agent_call=lambda s, u: VALID_PROMPTS_JSON)

    assert response.status.value == "NEEDS_INPUT"
    assert response.needs_input[0].reason_code == "no_prompts_produced"


def test_unsafe_content_flagged_needs_input(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    beats, candidates = _make_inputs({"b1": "06_fallback_generation"})
    _write(input_dir, beats, candidates)
    unsafe_json = json.dumps(
        {
            "prompts": [
                {
                    "beat_id": "b1",
                    "image_prompt": "a scene with blood and gore, cinematic",
                    "negative_prompt": "text, watermark, logo, blurry, extra limbs, distorted anatomy, low quality",
                    "rationale": "r",
                }
            ]
        }
    )

    response = run.main(input_dir, output_dir, RUN_CONFIG, agent_call=lambda s, u: unsafe_json)

    assert response.status.value == "NEEDS_INPUT"
    assert response.needs_input[0].reason_code == "unsafe_content_flagged"


def test_render_failure_fails(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    beats, candidates = _make_inputs({"b1": "06_fallback_generation"})
    _write(input_dir, beats, candidates)

    def _failing_zoompan(image_path, output_path, duration_s):
        raise FFmpegError("simulated zoompan failure")

    response = run.main(
        input_dir, output_dir, RUN_CONFIG,
        agent_call=lambda s, u: VALID_PROMPTS_JSON,
        image_generator=_noop_image_generator,
        zoompan=_failing_zoompan,
    )

    assert response.status.value == "FAILED"


def _noop_mood_visual_renderer(mood_tags: list, duration_s: float, dest_path: Path) -> None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(b"fake mp4")


def test_default_code_mode_generates_mood_visual_assets(tmp_path):
    # 2026-07-18: main() defaults to a lightweight ffmpeg visual (CODE) when
    # agent_call is omitted, not the real Ollama+sd-turbo backend - sd-turbo
    # proved too memory-heavy for a constrained dev machine. That default
    # was itself changed 2026-07-23 (see ARCHITECTURE.md change log) from an
    # on-screen text card to a mood-colored gradient with no text, since
    # 09_audio_production's TTS already speaks the same text aloud.
    run_id = "test_run_06"
    _clean_run_dir(run_id)
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    beats, candidates = _make_inputs({"b1": "06_fallback_generation"})
    _write(input_dir, beats, candidates)
    calls = []

    def recording_mood_visual_renderer(mood_tags, duration_s, dest_path):
        calls.append((mood_tags, duration_s))
        _noop_mood_visual_renderer(mood_tags, duration_s, dest_path)

    response = run.main(input_dir, output_dir, RUN_CONFIG, mood_visual_renderer=recording_mood_visual_renderer)

    assert response.status.value == "COMPLETE"
    out = json.loads((output_dir / "assets_manifest.json").read_text(encoding="utf-8"))
    assert out["assets"][0]["beat_id"] == "b1"
    assert out["assets"][0]["origin"] == "generated_fallback"
    assert "mood visual" in out["assets"][0]["license"]
    assert not (output_dir / "fallback_prompt.json").exists()  # no agent output in CODE mode
    # driven by the beat's own mood_tags, not an LLM prompt or on-screen text.
    # Duration is padded to config/fallback_visual.yaml's min_duration_s
    # (20.0), not the beat's raw est_duration_s (4.0) - a generated visual has
    # no natural length ceiling like real footage, and est_duration_s badly
    # underestimates real TTS narration length (see ARCHITECTURE.md).
    assert calls == [(["quiet", "tense"], 20.0)]
    assert out["assets"][0]["duration_s"] == 20.0

    _clean_run_dir(run_id)


def test_explicit_agent_call_still_routes_to_agent_diffusion_mode(tmp_path):
    # Agent+diffusion mode remains available by explicitly passing
    # agent_call - not deleted, just no longer the default.
    run_id = "test_run_06"
    _clean_run_dir(run_id)
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    beats, candidates = _make_inputs({"b1": "06_fallback_generation"})
    _write(input_dir, beats, candidates)
    calls = []

    def fake_agent_call(system_prompt, user_message):
        calls.append(1)
        return VALID_PROMPTS_JSON

    response = run.main(
        input_dir, output_dir, RUN_CONFIG,
        agent_call=fake_agent_call,
        image_generator=_noop_image_generator,
        zoompan=_noop_zoompan,
    )

    assert response.status.value == "COMPLETE"
    assert len(calls) == 1
    out = json.loads((output_dir / "assets_manifest.json").read_text(encoding="utf-8"))
    assert "sd-turbo" in out["assets"][0]["license"]
    assert (output_dir / "fallback_prompt.json").exists()

    _clean_run_dir(run_id)


def test_code_mode_render_failure_fails(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    beats, candidates = _make_inputs({"b1": "06_fallback_generation"})
    _write(input_dir, beats, candidates)

    def _failing_mood_visual_renderer(mood_tags, duration_s, dest_path):
        raise FFmpegError("simulated mood visual failure")

    response = run.main(input_dir, output_dir, RUN_CONFIG, mood_visual_renderer=_failing_mood_visual_renderer)

    assert response.status.value == "FAILED"


def test_missing_input_files_fails(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    input_dir.mkdir()

    response = run.main(input_dir, output_dir, RUN_CONFIG)

    assert response.status.value == "FAILED"
