from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

STAGE_SRC = Path(__file__).resolve().parents[1] / "src"
_spec = importlib.util.spec_from_file_location("stage02_2_scene_extraction_run", STAGE_SRC / "run.py")
run = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = run
_spec.loader.exec_module(run)

BASE_RUN_CONFIG = {"run_id": "test_run"}

SCREENPLAY = {
    "run_id": "test_run",
    "scene_id": "ch1_sc1",
    "elements": [
        {"type": "slugline", "text": "INT. BARN - NIGHT"},
        {"type": "action", "text": "The animals gather on the straw."},
        {"type": "slugline", "text": "EXT. FARMHOUSE - LATER"},
        {"type": "action", "text": "A lantern moves behind a window."},
    ],
}


def _write_screenplay(input_dir: Path, screenplay: dict = SCREENPLAY) -> None:
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "screenplay.json").write_text(json.dumps(screenplay), encoding="utf-8")


def _two_scene_segmentation() -> str:
    return json.dumps(
        {
            "scenes": [
                {"heading": "INT. BARN - NIGHT", "summary": "Animals gather.", "text": "The animals gather on the straw of the barn."},
                {"heading": "EXT. FARMHOUSE - LATER", "summary": "A lantern moves.", "text": "A lantern moves behind a farmhouse window in the dark."},
            ]
        }
    )


def test_render_system_prompt_includes_acting_sections():
    rendered = run._render_system_prompt(run.PROMPT_PATH.read_text(encoding="utf-8"))
    assert "## 1. Role" in rendered
    assert "## 9. Output Schema" in rendered
    assert "## 7. When Uncertain" not in rendered


def test_multi_scene_split_writes_manifest_and_files(tmp_path):
    in_dir, out_dir = tmp_path / "in", tmp_path / "out"
    _write_screenplay(in_dir)
    resp = run.main(in_dir, out_dir, BASE_RUN_CONFIG, agent_call=lambda s, u: _two_scene_segmentation())

    assert resp.status.value == "COMPLETE"
    manifest = json.loads((out_dir / "scenes_manifest.json").read_text(encoding="utf-8"))
    ids = [s["scene_id"] for s in manifest["scenes"]]
    assert ids == ["ch1_sc1_p1", "ch1_sc1_p2"]  # multi-scene -> suffixed
    assert manifest["scenes"][0]["chapter_number"] == 1
    assert manifest["scenes"][1]["scene_number_in_chapter"] == 2
    # Per-scene text files written and non-empty.
    assert (out_dir / "ch1_sc1_p1.txt").read_text(encoding="utf-8").strip()
    assert (out_dir / "ch1_sc1_p2.txt").read_text(encoding="utf-8").strip()


def test_single_scene_keeps_source_scene_id(tmp_path):
    in_dir, out_dir = tmp_path / "in", tmp_path / "out"
    _write_screenplay(in_dir)
    one = json.dumps({"scenes": [{"heading": "INT. BARN - NIGHT", "summary": "s", "text": "The animals gather in the barn."}]})
    resp = run.main(in_dir, out_dir, BASE_RUN_CONFIG, agent_call=lambda s, u: one)

    assert resp.status.value == "COMPLETE"
    manifest = json.loads((out_dir / "scenes_manifest.json").read_text(encoding="utf-8"))
    assert [s["scene_id"] for s in manifest["scenes"]] == ["ch1_sc1"]  # not suffixed
    assert (out_dir / "ch1_sc1.txt").exists()


def test_empty_scene_text_routes_needs_input(tmp_path):
    in_dir, out_dir = tmp_path / "in", tmp_path / "out"
    _write_screenplay(in_dir)
    bad = json.dumps({"scenes": [{"heading": "INT. BARN", "summary": "s", "text": ""}]})
    resp = run.main(in_dir, out_dir, BASE_RUN_CONFIG, agent_call=lambda s, u: bad)

    assert resp.status.value == "NEEDS_INPUT"
    assert resp.needs_input[0].reason_code == "scene_missing_text"


def test_text_equal_to_heading_routes_needs_input(tmp_path):
    in_dir, out_dir = tmp_path / "in", tmp_path / "out"
    _write_screenplay(in_dir)
    bad = json.dumps({"scenes": [{"heading": "INT. BARN", "summary": "s", "text": "INT. BARN"}]})
    resp = run.main(in_dir, out_dir, BASE_RUN_CONFIG, agent_call=lambda s, u: bad)

    assert resp.status.value == "NEEDS_INPUT"
    assert resp.needs_input[0].reason_code == "scene_missing_text"


def test_invalid_json_routes_needs_input(tmp_path):
    in_dir, out_dir = tmp_path / "in", tmp_path / "out"
    _write_screenplay(in_dir)
    resp = run.main(in_dir, out_dir, BASE_RUN_CONFIG, agent_call=lambda s, u: "garbage")
    assert resp.status.value == "NEEDS_INPUT"
    assert resp.needs_input[0].reason_code == "no_scenes_segmented"


def test_missing_screenplay_fails(tmp_path):
    in_dir, out_dir = tmp_path / "in", tmp_path / "out"
    in_dir.mkdir()
    resp = run.main(in_dir, out_dir, BASE_RUN_CONFIG, agent_call=lambda s, u: _two_scene_segmentation())
    assert resp.status.value == "FAILED"
