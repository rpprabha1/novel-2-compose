import importlib.util
import json
import sys
from pathlib import Path

# Loaded via a unique module name (not plain "import run") because every stage's
# tests/test_run.py imports its own src/run.py under the same bare name "run" -
# without this, sys.modules caching means the first stage's run.py wins for the
# whole pytest process when multiple stages' tests run together.
STAGE_SRC = Path(__file__).resolve().parents[1] / "src"
_spec = importlib.util.spec_from_file_location("stage01_manuscript_ingestion_run", STAGE_SRC / "run.py")
run = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = run
_spec.loader.exec_module(run)

REPO_ROOT = Path(__file__).resolve().parents[3]
SAMPLE_SCENE_PATH = REPO_ROOT / "shared" / "fixtures" / "sample_scene.txt"

BASE_RUN_CONFIG = {
    "run_id": "test_run",
    "manuscript_ref": "irrelevant-for-unit-test",
    "tone": "gothic-suspense",
    "pacing": "slow-burn",
    "music_intensity_curve": "rising",
    "scene_marker_convention": {"chapter_marker": "## Chapter", "scene_marker": "### Scene"},
    "pov_character": "Elena",
}

SYNTHETIC_MANUSCRIPT = """## Chapter 1

### Scene 1
Paragraph one of scene one.

Paragraph two of scene one.

### Scene 2
Paragraph one of scene two.

## Chapter 2

### Scene 1
Paragraph one of chapter two scene one.
"""


def test_split_manuscript_basic():
    scenes = run.split_manuscript(SYNTHETIC_MANUSCRIPT, "## Chapter", "### Scene")
    assert len(scenes) == 3
    assert scenes[0]["chapter_number"] == 1
    assert scenes[0]["scene_number_in_chapter"] == 1
    assert "Paragraph one of scene one." in scenes[0]["body"]
    assert scenes[1]["scene_number_in_chapter"] == 2
    assert scenes[2]["chapter_number"] == 2
    assert scenes[2]["scene_number_in_chapter"] == 1


def test_main_complete_writes_valid_outputs(tmp_path):
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    input_dir.mkdir()
    (input_dir / "manuscript.txt").write_text(SYNTHETIC_MANUSCRIPT, encoding="utf-8")

    response = run.main(input_dir, output_dir, BASE_RUN_CONFIG)

    assert response.status.value == "COMPLETE"
    assert (output_dir / "ch1_sc1.txt").exists()
    assert (output_dir / "ch1_sc2.txt").exists()
    assert (output_dir / "ch2_sc1.txt").exists()

    manifest = json.loads((output_dir / "scenes_manifest.json").read_text(encoding="utf-8"))
    assert [s["scene_id"] for s in manifest["scenes"]] == ["ch1_sc1", "ch1_sc2", "ch2_sc1"]
    assert manifest["scenes"][0]["pov_character"] == "Elena"
    assert manifest["scenes"][0]["order"] == 0


def test_main_against_standing_fixture(tmp_path):
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    input_dir.mkdir()
    (input_dir / "manuscript.txt").write_text(
        SAMPLE_SCENE_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )

    response = run.main(input_dir, output_dir, BASE_RUN_CONFIG)

    assert response.status.value == "COMPLETE"
    scene_file = output_dir / "ch1_sc1.txt"
    assert scene_file.exists()
    assert scene_file.read_text(encoding="utf-8").startswith(
        "Elena climbed the narrow attic staircase"
    )


def test_main_missing_manuscript_fails(tmp_path):
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    output_dir = tmp_path / "outputs"

    response = run.main(input_dir, output_dir, BASE_RUN_CONFIG)

    assert response.status.value == "FAILED"
    assert response.error is not None


def test_main_no_markers_needs_input(tmp_path):
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    input_dir.mkdir()
    (input_dir / "manuscript.txt").write_text("Just plain text, no markers at all.", encoding="utf-8")

    response = run.main(input_dir, output_dir, BASE_RUN_CONFIG)

    assert response.status.value == "NEEDS_INPUT"
    assert response.needs_input[0].reason_code == "no_scenes_found"
