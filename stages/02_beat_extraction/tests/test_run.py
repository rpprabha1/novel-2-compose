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
                    "visual_description": "desc",
                    "est_duration_s": 3.0,
                    "mood_tags": ["quiet"],
                    "no_visual_analog": True,
                },
                {
                    "beat_id": "ch1_sc1_b002",
                    "order": 1,
                    "text_excerpt_ref": "para:2",
                    "visual_description": "desc",
                    "est_duration_s": 3.0,
                    "mood_tags": ["quiet"],
                    "no_visual_analog": True,
                },
                {
                    "beat_id": "ch1_sc1_b003",
                    "order": 2,
                    "text_excerpt_ref": "para:3",
                    "visual_description": "desc",
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
