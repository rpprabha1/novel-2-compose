from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

# See stage 01's test_run.py for why this isn't a plain "import run".
STAGE_SRC = Path(__file__).resolve().parents[1] / "src"
_spec = importlib.util.spec_from_file_location("stage02_beat_extraction_run", STAGE_SRC / "run.py")
run = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = run
_spec.loader.exec_module(run)

BASE_RUN_CONFIG = {
    "run_id": "test_run",
    "tone": "gothic-suspense",
    "pacing": "slow-burn",
}

SCENE_TEXT = (
    "A woman climbs a narrow attic staircase, dust motes drifting through a shaft of light.\n\n"
    "She kneels and opens an old trunk, finding photographs and a letter inside."
)

VALID_BEATS_JSON = json.dumps(
    {
        "beats": [
            {
                "beat_id": "ch1_sc1_b001",
                "order": 0,
                "text_excerpt_ref": "para:1",
                "visual_description": "A woman climbs a narrow attic staircase, dust motes drifting through light.",
                "est_duration_s": 4.0,
                "mood_tags": ["quiet", "tense"],
                "no_visual_analog": False,
            },
            {
                "beat_id": "ch1_sc1_b002",
                "order": 1,
                "text_excerpt_ref": "para:2",
                "visual_description": "She kneels and opens an old trunk, finding photographs and a letter.",
                "est_duration_s": 4.5,
                "mood_tags": ["quiet"],
                "no_visual_analog": False,
            },
        ]
    }
)


def _write_scene(input_dir: Path, scene_id: str = "ch1_sc1", text: str = SCENE_TEXT) -> None:
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / f"{scene_id}.txt").write_text(text, encoding="utf-8")


def test_render_system_prompt_excludes_meta_sections():
    prompt_md = run.PROMPT_PATH.read_text(encoding="utf-8")
    rendered = run._render_system_prompt(prompt_md)
    assert "## 1. Role" in rendered
    assert "## 9. Output Schema" in rendered
    # Section 7's NEEDS_INPUT process text is Coordinator-facing, not model-facing.
    assert "no_scene_beats_produced" not in rendered
    assert "## 12. Definition of Done" not in rendered


def test_strip_wrapper_handles_markdown_fence():
    wrapped = "Here you go:\n```json\n{\"a\": 1}\n```\nHope that helps!"
    assert run._strip_wrapper(wrapped) == '{"a": 1}'


def test_main_complete_with_mocked_agent(tmp_path):
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    _write_scene(input_dir)

    response = run.main(input_dir, output_dir, BASE_RUN_CONFIG, agent_call=lambda s, u: VALID_BEATS_JSON)

    assert response.status.value == "COMPLETE"
    beats = json.loads((output_dir / "beats.json").read_text(encoding="utf-8"))
    assert beats["scene_id"] == "ch1_sc1"
    assert len(beats["beats"]) == 2


def test_main_invalid_json_needs_input(tmp_path):
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    _write_scene(input_dir)

    response = run.main(input_dir, output_dir, BASE_RUN_CONFIG, agent_call=lambda s, u: "not json at all")

    assert response.status.value == "NEEDS_INPUT"
    assert response.needs_input[0].reason_code == "no_scene_beats_produced"


def test_main_bad_mood_tag_needs_input(tmp_path):
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    _write_scene(input_dir)
    bad_json = json.dumps(
        {
            "beats": [
                {
                    "beat_id": "ch1_sc1_b001",
                    "order": 0,
                    "text_excerpt_ref": "para:1",
                    "visual_description": "desc",
                    "est_duration_s": 3.0,
                    "mood_tags": ["scary"],
                    "no_visual_analog": False,
                }
            ]
        }
    )

    response = run.main(input_dir, output_dir, BASE_RUN_CONFIG, agent_call=lambda s, u: bad_json)

    assert response.status.value == "NEEDS_INPUT"
    assert response.needs_input[0].reason_code == "mood_tag_outside_vocabulary"


def test_main_coerces_partial_invalid_mood_tags(tmp_path):
    """Out-of-vocabulary tags are dropped mechanically; a beat left empty
    gets the scene's most common valid tag (2026-07-23 strict-code-over-
    prompt policy) - the run COMPLETEs instead of blocking on NEEDS_INPUT."""
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    _write_scene(input_dir)
    mixed_json = json.dumps(
        {
            "beats": [
                {
                    "beat_id": "ch1_sc1_b001",
                    "order": 0,
                    "text_excerpt_ref": "para:1",
                    "visual_description": "A woman climbs a narrow attic staircase.",
                    "est_duration_s": 3.0,
                    "mood_tags": ["quiet", "majestic"],
                    "no_visual_analog": False,
                },
                {
                    "beat_id": "ch1_sc1_b002",
                    "order": 1,
                    "text_excerpt_ref": "para:2",
                    "visual_description": "She kneels and opens an old trunk.",
                    "est_duration_s": 3.0,
                    "mood_tags": ["drunk"],
                    "no_visual_analog": False,
                },
            ]
        }
    )

    response = run.main(input_dir, output_dir, BASE_RUN_CONFIG, agent_call=lambda s, u: mixed_json)

    assert response.status.value == "COMPLETE"
    assert "majestic" in response.summary and "drunk" in response.summary
    beats = json.loads((output_dir / "beats.json").read_text(encoding="utf-8"))["beats"]
    assert beats[0]["mood_tags"] == ["quiet"]
    assert beats[1]["mood_tags"] == ["quiet"]  # backfilled with the scene's most common valid tag


def test_main_out_of_range_paragraph_ref_needs_input(tmp_path):
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    _write_scene(input_dir)
    invented_trailing_beat_json = json.dumps(
        {
            "beats": [
                {
                    "beat_id": "ch1_sc1_b001",
                    "order": 0,
                    "text_excerpt_ref": "para:1",
                    "visual_description": "desc",
                    "est_duration_s": 3.0,
                    "mood_tags": ["quiet"],
                    "no_visual_analog": False,
                },
                {
                    "beat_id": "ch1_sc1_b002",
                    "order": 1,
                    "text_excerpt_ref": "para:3",
                    "visual_description": "desc",
                    "est_duration_s": 3.0,
                    "mood_tags": ["quiet"],
                    "no_visual_analog": False,
                },
            ]
        }
    )

    response = run.main(input_dir, output_dir, BASE_RUN_CONFIG, agent_call=lambda s, u: invented_trailing_beat_json)

    assert response.status.value == "NEEDS_INPUT"
    assert response.needs_input[0].reason_code == "invalid_beat_grounding"


def test_main_empty_visual_description_needs_input(tmp_path):
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    _write_scene(input_dir)
    empty_desc_json = json.dumps(
        {
            "beats": [
                {
                    "beat_id": "ch1_sc1_b001",
                    "order": 0,
                    "text_excerpt_ref": "para:1",
                    "visual_description": "",
                    "est_duration_s": 3.0,
                    "mood_tags": ["quiet"],
                    "no_visual_analog": True,
                }
            ]
        }
    )

    response = run.main(input_dir, output_dir, BASE_RUN_CONFIG, agent_call=lambda s, u: empty_desc_json)

    assert response.status.value == "NEEDS_INPUT"
    assert response.needs_input[0].reason_code == "invalid_beat_grounding"


def test_main_majority_no_visual_analog_needs_input(tmp_path):
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    _write_scene(input_dir)
    sparse_json = json.dumps(
        {
            "beats": [
                {
                    "beat_id": "ch1_sc1_b001",
                    "order": 0,
                    "text_excerpt_ref": "para:1",
                    "visual_description": "No direct visual - interior reflection on the day's events.",
                    "est_duration_s": 3.0,
                    "mood_tags": ["quiet"],
                    "no_visual_analog": True,
                },
                {
                    "beat_id": "ch1_sc1_b002",
                    "order": 1,
                    "text_excerpt_ref": "para:2",
                    "visual_description": "No direct visual - a memory of childhood surfaces unbidden.",
                    "est_duration_s": 3.0,
                    "mood_tags": ["quiet"],
                    "no_visual_analog": True,
                },
                {
                    "beat_id": "ch1_sc1_b003",
                    "order": 2,
                    "text_excerpt_ref": "para:2",
                    "visual_description": "She sets down the cup and looks toward the window.",
                    "est_duration_s": 3.0,
                    "mood_tags": ["quiet"],
                    "no_visual_analog": False,
                },
            ]
        }
    )

    response = run.main(input_dir, output_dir, BASE_RUN_CONFIG, agent_call=lambda s, u: sparse_json)

    assert response.status.value == "NEEDS_INPUT"
    assert response.needs_input[0].reason_code == "majority_no_visual_analog"


def test_main_merges_adjacent_duplicate_visual_beats(tmp_path):
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    four_para_text = (
        "A crowd gathers in the square, murmuring.\n\n"
        "The choir begins to sing the old anthem together.\n\n"
        "The choir keeps singing the old anthem together.\n\n"
        "The crowd disperses into the evening streets."
    )
    _write_scene(input_dir, text=four_para_text)
    duplicate_visual_json = json.dumps(
        {
            "beats": [
                {
                    "beat_id": "ch1_sc1_b001",
                    "order": 0,
                    "text_excerpt_ref": "para:1",
                    "visual_description": "A crowd gathers in a square, murmuring among themselves.",
                    "est_duration_s": 3.0,
                    "mood_tags": ["quiet"],
                    "no_visual_analog": False,
                },
                {
                    "beat_id": "ch1_sc1_b002",
                    "order": 1,
                    "text_excerpt_ref": "para:2",
                    "visual_description": "The choir sings together in the square.",
                    "est_duration_s": 4.0,
                    "mood_tags": ["triumphant"],
                    "no_visual_analog": False,
                },
                {
                    "beat_id": "ch1_sc1_b003",
                    "order": 2,
                    "text_excerpt_ref": "para:3",
                    "visual_description": "The choir sings together in the square.",
                    "est_duration_s": 4.0,
                    "mood_tags": ["playful"],
                    "no_visual_analog": False,
                },
                {
                    "beat_id": "ch1_sc1_b004",
                    "order": 3,
                    "text_excerpt_ref": "para:4",
                    "visual_description": "The crowd disperses down the evening streets.",
                    "est_duration_s": 3.0,
                    "mood_tags": ["quiet"],
                    "no_visual_analog": False,
                },
            ]
        }
    )

    response = run.main(input_dir, output_dir, BASE_RUN_CONFIG, agent_call=lambda s, u: duplicate_visual_json)

    assert response.status.value == "COMPLETE"
    beats = json.loads((output_dir / "beats.json").read_text(encoding="utf-8"))["beats"]
    assert len(beats) == 3
    merged = beats[1]
    assert merged["text_excerpt_ref"] == "para:2-3"
    assert merged["est_duration_s"] == 8.0
    assert set(merged["mood_tags"]) == {"triumphant", "playful"}
    assert [b["beat_id"] for b in beats] == ["ch1_sc1_b001", "ch1_sc1_b002", "ch1_sc1_b003"]
    assert [b["order"] for b in beats] == [0, 1, 2]


def test_main_missing_scene_file_fails(tmp_path):
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    output_dir = tmp_path / "outputs"

    response = run.main(input_dir, output_dir, BASE_RUN_CONFIG, agent_call=lambda s, u: VALID_BEATS_JSON)

    assert response.status.value == "FAILED"


def test_main_agent_backend_error_fails(tmp_path):
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    _write_scene(input_dir)

    def _raise(system_prompt, user_message):
        raise run.AgentBackendError("connection refused")

    response = run.main(input_dir, output_dir, BASE_RUN_CONFIG, agent_call=_raise)

    assert response.status.value == "FAILED"
    assert "connection refused" in response.error.diagnostics
