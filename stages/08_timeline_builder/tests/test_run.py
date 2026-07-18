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


def _audio_mix(narration_specs: list[tuple[str, float, float]]) -> dict:
    return {
        "run_id": "test_run_08",
        "scene_id": "ch1_sc1",
        "narration_stems": [
            {"beat_id": bid, "file_ref": f"cache/{bid}.wav", "start_s": start, "duration_s": dur}
            for bid, start, dur in narration_specs
        ],
        "music_stems": [],
        "mix_params": {"ducking_depth_db": -12, "ducking_attack_ms": 150},
        "final_lufs": -16.0,
    }


def _write(input_dir: Path, edit_plan: dict, assets: dict, audio_mix: dict | None = None) -> None:
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "edit_plan.json").write_text(json.dumps(edit_plan), encoding="utf-8")
    (input_dir / "assets_manifest.json").write_text(json.dumps(assets), encoding="utf-8")
    if audio_mix is not None:
        (input_dir / "audio_mix.json").write_text(json.dumps(audio_mix), encoding="utf-8")


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
    assert clips[0]["transition_out"] == {"type": "crossfade", "duration_s": 0.75}  # real config value
    assert clips[1]["timeline_start_s"] == 3.0
    assert clips[1]["timeline_end_s"] == 7.0
    assert "transition_out" not in clips[1]  # last clip overall - nothing follows
    assert out["total_duration_s"] == 7.0


def test_transition_duration_from_injected_vocab(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    edit_plan = {
        "run_id": "test_run_08",
        "scene_id": "ch1_sc1",
        "beats": [
            {"beat_id": "b1", "asset_id": "a1", "shots": [{"shot_id": "b1_s1", "in_s": 0.0, "out_s": 5.0, "hold_duration_s": 3.0}], "transition_out": "dip-to-black", "rationale": ""},
            {"beat_id": "b2", "asset_id": "a2", "shots": [{"shot_id": "b2_s1", "in_s": 0.0, "out_s": 5.0, "hold_duration_s": 4.0}], "transition_out": "hard-cut", "rationale": ""},
        ],
    }
    assets = _assets([("a1", 10.0), ("a2", 10.0)])
    _write(input_dir, edit_plan, assets)
    vocab = {"transition_durations_s": {"hard-cut": 0.0, "crossfade": 0.75, "dip-to-black": 1.23, "match-cut-suggestion": 0.0}}

    response = run.main(input_dir, output_dir, RUN_CONFIG, vocab=vocab)

    assert response.status.value == "COMPLETE"
    out = json.loads((output_dir / "timeline.json").read_text(encoding="utf-8"))
    assert out["clips"][0]["transition_out"] == {"type": "dip-to-black", "duration_s": 1.23}


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


def test_multi_shot_beat_resolves_per_shot_asset(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    edit_plan = {
        "run_id": "test_run_08",
        "scene_id": "ch1_sc1",
        "beats": [
            {
                "beat_id": "b1",
                "asset_id": "a1",
                "shots": [
                    {"shot_id": "b1_s1", "in_s": 0.0, "out_s": 4.0, "hold_duration_s": 4.0},
                    {"shot_id": "b1_s2", "asset_id": "a2", "in_s": 0.0, "out_s": 4.0, "hold_duration_s": 4.0},
                ],
                "transition_out": "hard-cut",
                "rationale": "",
            }
        ],
    }
    assets = _assets([("a1", 20.0), ("a2", 20.0)])
    _write(input_dir, edit_plan, assets)

    response = run.main(input_dir, output_dir, RUN_CONFIG)

    assert response.status.value == "COMPLETE"
    out = json.loads((output_dir / "timeline.json").read_text(encoding="utf-8"))
    clips = out["clips"]
    assert clips[0]["file_ref"] == "cache/a1.mp4"
    assert clips[1]["file_ref"] == "cache/a2.mp4"
    assert clips[0]["transition_out"] == {"type": "hard-cut", "duration_s": 0.0}  # intra-beat
    assert out["total_duration_s"] == 8.0


def test_shot_asset_id_not_in_manifest_fails(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    edit_plan = {
        "run_id": "test_run_08",
        "scene_id": "ch1_sc1",
        "beats": [
            {
                "beat_id": "b1",
                "asset_id": "a1",
                "shots": [{"shot_id": "b1_s1", "asset_id": "missing_asset", "in_s": 0.0, "out_s": 4.0, "hold_duration_s": 4.0}],
                "transition_out": "hard-cut",
                "rationale": "",
            }
        ],
    }
    assets = _assets([("a1", 20.0)])
    _write(input_dir, edit_plan, assets)

    response = run.main(input_dir, output_dir, RUN_CONFIG)

    assert response.status.value == "FAILED"


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


def test_narration_reconciliation_extends_hold_when_asset_covers_it(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    edit_plan = {
        "run_id": "test_run_08",
        "scene_id": "ch1_sc1",
        "beats": [{"beat_id": "b1", "asset_id": "a1", "shots": [{"shot_id": "b1_s1", "in_s": 0.0, "out_s": 5.0, "hold_duration_s": 3.0}], "transition_out": "hard-cut", "rationale": ""}],
    }
    assets = _assets([("a1", 39.0)])  # plenty of room for narration
    audio_mix = _audio_mix([("b1", 0.0, 14.7)])  # narration needs 14.7s, well beyond the 3.0s visual hold
    _write(input_dir, edit_plan, assets, audio_mix)

    response = run.main(input_dir, output_dir, RUN_CONFIG)

    assert response.status.value == "COMPLETE"
    out = json.loads((output_dir / "timeline.json").read_text(encoding="utf-8"))
    assert out["clips"][0]["timeline_end_s"] == 14.7
    assert out["clips"][0]["source_out_s"] == 14.7
    assert out["total_duration_s"] == 14.7


def test_narration_reconciliation_leaves_hold_alone_when_already_covers_it(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    edit_plan = {
        "run_id": "test_run_08",
        "scene_id": "ch1_sc1",
        "beats": [{"beat_id": "b1", "asset_id": "a1", "shots": [{"shot_id": "b1_s1", "in_s": 0.0, "out_s": 5.0, "hold_duration_s": 3.0}], "transition_out": "hard-cut", "rationale": ""}],
    }
    assets = _assets([("a1", 39.0)])
    audio_mix = _audio_mix([("b1", 0.0, 2.0)])  # narration is shorter than the visual hold already
    _write(input_dir, edit_plan, assets, audio_mix)

    response = run.main(input_dir, output_dir, RUN_CONFIG)

    assert response.status.value == "COMPLETE"
    out = json.loads((output_dir / "timeline.json").read_text(encoding="utf-8"))
    assert out["clips"][0]["timeline_end_s"] == 3.0  # untouched - visual hold already sufficient


def test_narration_reconciliation_routes_fallback_when_asset_too_short(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    edit_plan = {
        "run_id": "test_run_08",
        "scene_id": "ch1_sc1",
        "beats": [{"beat_id": "b1", "asset_id": "a1", "shots": [{"shot_id": "b1_s1", "in_s": 0.0, "out_s": 5.0, "hold_duration_s": 3.0}], "transition_out": "hard-cut", "rationale": ""}],
    }
    assets = _assets([("a1", 6.0)])  # too short for the narration below
    audio_mix = _audio_mix([("b1", 0.0, 13.18)])
    _write(input_dir, edit_plan, assets, audio_mix)

    response = run.main(input_dir, output_dir, RUN_CONFIG)

    assert response.status.value == "FALLBACK_ROUTED"
    assert response.fallback_routed[0].reason_code == "asset_too_short_for_narration"
    assert response.fallback_routed[0].item_id == "b1"
    assert not (output_dir / "timeline.json").exists()


def test_narration_reconciliation_scales_multi_shot_beat_proportionally(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    edit_plan = {
        "run_id": "test_run_08",
        "scene_id": "ch1_sc1",
        "beats": [
            {
                "beat_id": "b1",
                "asset_id": "a1",
                "shots": [
                    {"shot_id": "b1_s1", "in_s": 0.0, "out_s": 10.0, "hold_duration_s": 1.0},
                    {"shot_id": "b1_s2", "in_s": 10.0, "out_s": 20.0, "hold_duration_s": 3.0},
                ],
                "transition_out": "hard-cut",
                "rationale": "",
            }
        ],
    }
    assets = _assets([("a1", 39.0)])
    audio_mix = _audio_mix([("b1", 0.0, 8.0)])  # 2x the original 4.0s total -> each shot should double

    _write(input_dir, edit_plan, assets, audio_mix)

    response = run.main(input_dir, output_dir, RUN_CONFIG)

    assert response.status.value == "COMPLETE"
    out = json.loads((output_dir / "timeline.json").read_text(encoding="utf-8"))
    assert out["clips"][0]["timeline_end_s"] == 2.0  # 1.0 * 2
    assert out["clips"][1]["timeline_end_s"] == 8.0  # 2.0 + (3.0 * 2)
