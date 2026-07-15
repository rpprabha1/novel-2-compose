import importlib.util
import json
import sys
from pathlib import Path

# See stage 01's test_run.py for why this isn't a plain "import run".
STAGE_SRC = Path(__file__).resolve().parents[1] / "src"
_spec = importlib.util.spec_from_file_location("stage08_timeline_builder_run", STAGE_SRC / "run.py")
run = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = run
_spec.loader.exec_module(run)

RUN_CONFIG = {"run_id": "test_run_08"}


def _assets(specs: list[tuple[str, float]]) -> dict:
    return {
        "run_id": "test_run_08",
        "scene_id": "ch1_sc1",
        "assets": [
            {
                "beat_id": f"b_for_{aid}",
                "asset_id": aid,
                "origin": "retrieved_verified",
                "file_ref": f"cache/{aid}.mp4",
                "duration_s": dur,
                "license": "Pexels License",
                "attribution": {"source": "pexels", "creator_required": False},
            }
            for aid, dur in specs
        ],
    }


def _write(input_dir: Path, edit_plan: dict, assets: dict) -> None:
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "edit_plan.json").write_text(json.dumps(edit_plan), encoding="utf-8")
    (input_dir / "assets_manifest.json").write_text(json.dumps(assets), encoding="utf-8")


def test_complete_happy_path_sequential_timeline(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    edit_plan = {
        "run_id": "test_run_08",
        "scene_id": "ch1_sc1",
        "beats": [
            {"beat_id": "b1", "asset_id": "a1", "shots": [{"shot_id": "b1_s1", "in_s": 0.0, "out_s": 5.0, "hold_duration_s": 3.0}], "transition_out": "crossfade", "rationale": ""},
            {"beat_id": "b2", "asset_id": "a2", "shots": [{"shot_id": "b2_s1", "in_s": 0.0, "out_s": 5.0, "hold_duration_s": 4.0}], "transition_out": "hard-cut", "rationale": ""},
        ],
    }
    assets = _assets([("a1", 10.0), ("a2", 10.0)])
    _write(input_dir, edit_plan, assets)

    response = run.main(input_dir, output_dir, RUN_CONFIG)

    assert response.status.value == "COMPLETE"
    out = json.loads((output_dir / "timeline.json").read_text(encoding="utf-8"))
    clips = out["clips"]
    assert clips[0]["timeline_start_s"] == 0.0
    assert clips[0]["timeline_end_s"] == 3.0
    assert clips[0]["transition_out"] == {"type": "crossfade", "duration_s": 0.0}
    assert clips[1]["timeline_start_s"] == 3.0
    assert clips[1]["timeline_end_s"] == 7.0
    assert "transition_out" not in clips[1]  # last clip overall - nothing follows
    assert out["total_duration_s"] == 7.0


def test_multi_shot_beat_intra_beat_hard_cut(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    edit_plan = {
        "run_id": "test_run_08",
        "scene_id": "ch1_sc1",
        "beats": [
            {
                "beat_id": "b1",
                "asset_id": "a1",
                "shots": [
                    {"shot_id": "b1_s1", "in_s": 0.0, "out_s": 5.0, "hold_duration_s": 2.0},
                    {"shot_id": "b1_s2", "in_s": 5.0, "out_s": 10.0, "hold_duration_s": 2.0},
                ],
                "transition_out": "dip-to-black",
                "rationale": "",
            }
        ],
    }
    assets = _assets([("a1", 20.0)])
    _write(input_dir, edit_plan, assets)

    response = run.main(input_dir, output_dir, RUN_CONFIG)

    assert response.status.value == "COMPLETE"
    out = json.loads((output_dir / "timeline.json").read_text(encoding="utf-8"))
    clips = out["clips"]
    assert clips[0]["transition_out"] == {"type": "hard-cut", "duration_s": 0.0}  # intra-beat
    assert "transition_out" not in clips[1]  # last shot of last (only) beat


def test_source_out_s_trims_to_hold_duration_not_out_s(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    # in_s=0, out_s=4.5 (available window), hold_duration_s=2.75 (actual screen time)
    edit_plan = {
        "run_id": "test_run_08",
        "scene_id": "ch1_sc1",
        "beats": [{"beat_id": "b1", "asset_id": "a1", "shots": [{"shot_id": "b1_s1", "in_s": 0.0, "out_s": 4.5, "hold_duration_s": 2.75}], "transition_out": "hard-cut", "rationale": ""}],
    }
    assets = _assets([("a1", 39.0)])
    _write(input_dir, edit_plan, assets)

    response = run.main(input_dir, output_dir, RUN_CONFIG)

    assert response.status.value == "COMPLETE"
    out = json.loads((output_dir / "timeline.json").read_text(encoding="utf-8"))
    assert out["clips"][0]["source_out_s"] == 2.75  # not 4.5


def test_missing_asset_fails(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    edit_plan = {
        "run_id": "test_run_08",
        "scene_id": "ch1_sc1",
        "beats": [{"beat_id": "b1", "asset_id": "missing_asset", "shots": [{"shot_id": "b1_s1", "in_s": 0.0, "out_s": 5.0, "hold_duration_s": 3.0}], "transition_out": "hard-cut", "rationale": ""}],
    }
    assets = _assets([])
    _write(input_dir, edit_plan, assets)

    response = run.main(input_dir, output_dir, RUN_CONFIG)

    assert response.status.value == "FAILED"


def test_hold_exceeding_asset_duration_fails(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    edit_plan = {
        "run_id": "test_run_08",
        "scene_id": "ch1_sc1",
        "beats": [{"beat_id": "b1", "asset_id": "a1", "shots": [{"shot_id": "b1_s1", "in_s": 0.0, "out_s": 5.0, "hold_duration_s": 3.0}], "transition_out": "hard-cut", "rationale": ""}],
    }
    assets = _assets([("a1", 2.0)])  # asset shorter than the shot needs
    _write(input_dir, edit_plan, assets)

    response = run.main(input_dir, output_dir, RUN_CONFIG)

    assert response.status.value == "FAILED"


def test_missing_input_files_fails(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    input_dir.mkdir()

    response = run.main(input_dir, output_dir, RUN_CONFIG)

    assert response.status.value == "FAILED"
