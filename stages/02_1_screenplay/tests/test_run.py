from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

# See stage 01's test_run.py for why this isn't a plain "import run".
STAGE_SRC = Path(__file__).resolve().parents[1] / "src"
_spec = importlib.util.spec_from_file_location("stage02_1_screenplay_run", STAGE_SRC / "run.py")
run = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = run
_spec.loader.exec_module(run)

BASE_RUN_CONFIG = {"run_id": "test_run", "tone": "gothic-suspense"}

SCENE_TEXT = (
    "The animals gathered in the great barn as the old boar settled onto the platform.\n\n"
    '"Comrades," he began, "I have had a strange dream."'
)

VALID_SCREENPLAY = json.dumps(
    {
        "elements": [
            {"type": "slugline", "text": "INT. BIG BARN - NIGHT"},
            {"type": "action", "text": "The animals gather as an old boar settles onto a platform."},
            {"type": "dialogue", "text": "Comrades, I have had a strange dream.", "character": "MAJOR"},
        ]
    }
)


def _write_scene(input_dir: Path, scene_id: str = "ch1_sc1", text: str = SCENE_TEXT) -> None:
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / f"{scene_id}.txt").write_text(text, encoding="utf-8")


def test_render_system_prompt_includes_acting_sections_excludes_meta():
    rendered = run._render_system_prompt(run.PROMPT_PATH.read_text(encoding="utf-8"))
    assert "## 1. Role" in rendered
    assert "## 9. Output Schema" in rendered
    assert "## 8. HITL Triggers" not in rendered  # meta section stripped


def test_happy_path_writes_valid_screenplay(tmp_path):
    in_dir, out_dir = tmp_path / "in", tmp_path / "out"
    _write_scene(in_dir)
    resp = run.main(in_dir, out_dir, BASE_RUN_CONFIG, agent_call=lambda s, u: VALID_SCREENPLAY)

    assert resp.status.value == "COMPLETE"
    written = json.loads((out_dir / "screenplay.json").read_text(encoding="utf-8"))
    assert written["run_id"] == "test_run"  # forced from context
    assert written["scene_id"] == "ch1_sc1"
    assert [e["type"] for e in written["elements"]] == ["slugline", "action", "dialogue"]


def test_scene_id_and_run_id_forced_over_model_output(tmp_path):
    in_dir, out_dir = tmp_path / "in", tmp_path / "out"
    _write_scene(in_dir, scene_id="ch2_sc3")
    lying = json.dumps({"run_id": "WRONG", "scene_id": "WRONG", "elements": [{"type": "slugline", "text": "EXT. FIELD - DAY"}]})
    resp = run.main(in_dir, out_dir, BASE_RUN_CONFIG, agent_call=lambda s, u: lying)

    assert resp.status.value == "COMPLETE"
    written = json.loads((out_dir / "screenplay.json").read_text(encoding="utf-8"))
    assert written["run_id"] == "test_run"
    assert written["scene_id"] == "ch2_sc3"


def test_invalid_json_routes_needs_input(tmp_path):
    in_dir, out_dir = tmp_path / "in", tmp_path / "out"
    _write_scene(in_dir)
    resp = run.main(in_dir, out_dir, BASE_RUN_CONFIG, agent_call=lambda s, u: "not json at all")

    assert resp.status.value == "NEEDS_INPUT"
    assert resp.needs_input[0].reason_code == "no_screenplay_produced"


def test_dialogue_without_character_routes_invalid_structure(tmp_path):
    in_dir, out_dir = tmp_path / "in", tmp_path / "out"
    _write_scene(in_dir)
    bad = json.dumps({"elements": [{"type": "dialogue", "text": "hi"}]})  # no character
    resp = run.main(in_dir, out_dir, BASE_RUN_CONFIG, agent_call=lambda s, u: bad)

    assert resp.status.value == "NEEDS_INPUT"
    assert resp.needs_input[0].reason_code == "screenplay_invalid_structure"


def test_empty_elements_routes_needs_input(tmp_path):
    in_dir, out_dir = tmp_path / "in", tmp_path / "out"
    _write_scene(in_dir)
    resp = run.main(in_dir, out_dir, BASE_RUN_CONFIG, agent_call=lambda s, u: json.dumps({"elements": []}))

    assert resp.status.value == "NEEDS_INPUT"
    assert resp.needs_input[0].reason_code == "no_screenplay_produced"


def test_missing_scene_file_fails(tmp_path):
    in_dir, out_dir = tmp_path / "in", tmp_path / "out"
    in_dir.mkdir()
    resp = run.main(in_dir, out_dir, BASE_RUN_CONFIG, agent_call=lambda s, u: VALID_SCREENPLAY)
    assert resp.status.value == "FAILED"
