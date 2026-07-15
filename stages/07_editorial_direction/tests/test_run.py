import importlib.util
import json
import sys
from pathlib import Path

# See stage 01's test_run.py for why this isn't a plain "import run".
STAGE_SRC = Path(__file__).resolve().parents[1] / "src"
_spec = importlib.util.spec_from_file_location("stage07_editorial_direction_run", STAGE_SRC / "run.py")
run = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = run
_spec.loader.exec_module(run)

RUN_CONFIG = {"run_id": "test_run_07", "pacing": "standard"}

THRESHOLDS = {"editorial": {"min_viable_shot_length_s": 1.5, "max_runtime_drift_pct": 15}}
VOCAB = {
    "transition_families": ["hard-cut", "crossfade", "dip-to-black", "match-cut-suggestion"],
    "pacing_presets": {"standard": {"hold_duration_s": {"min": 1.5, "max": 4.0}, "max_shots_per_beat": 5}},
    "hitl_shot_subdivision_threshold": 3,
}


def _beats(specs: list[tuple[str, float]]) -> dict:
    return {
        "run_id": "test_run_07",
        "scene_id": "ch1_sc1",
        "beats": [
            {
                "beat_id": bid,
                "order": i,
                "text_excerpt_ref": f"para:{i + 1}",
                "visual_description": "desc",
                "est_duration_s": est,
                "mood_tags": ["quiet"],
                "no_visual_analog": False,
            }
            for i, (bid, est) in enumerate(specs)
        ],
    }


def _assets(specs: list[tuple[str, float]]) -> dict:
    return {
        "run_id": "test_run_07",
        "scene_id": "ch1_sc1",
        "assets": [
            {
                "beat_id": bid,
                "asset_id": f"asset_{bid}",
                "origin": "retrieved_verified",
                "file_ref": f"cache/{bid}.mp4",
                "duration_s": dur,
                "license": "Pexels License",
                "attribution": {"source": "pexels", "creator_required": False},
            }
            for bid, dur in specs
        ],
    }


def _plan_json(entries: list[dict]) -> str:
    return json.dumps({"beats": entries})


def _write(input_dir: Path, beats: dict, assets: dict) -> None:
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "beats.json").write_text(json.dumps(beats), encoding="utf-8")
    (input_dir / "assets_manifest.json").write_text(json.dumps(assets), encoding="utf-8")


def test_complete_happy_path(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    beats = _beats([("b1", 3.0), ("b2", 3.0)])
    assets = _assets([("b1", 10.0), ("b2", 10.0)])
    _write(input_dir, beats, assets)
    plan = _plan_json(
        [
            {"beat_id": "b1", "asset_id": "asset_b1", "shots": [{"shot_id": "b1_s1", "in_s": 0, "out_s": 3.0, "hold_duration_s": 3.0}], "transition_out": "hard-cut", "rationale": ""},
            {"beat_id": "b2", "asset_id": "asset_b2", "shots": [{"shot_id": "b2_s1", "in_s": 0, "out_s": 3.0, "hold_duration_s": 3.0}], "transition_out": "hard-cut", "rationale": ""},
        ]
    )

    response = run.main(input_dir, output_dir, RUN_CONFIG, agent_call=lambda s, u: plan, thresholds=THRESHOLDS, vocab=VOCAB)

    assert response.status.value == "COMPLETE"
    out = json.loads((output_dir / "edit_plan.json").read_text(encoding="utf-8"))
    assert out["total_runtime_s"] == 6.0


def test_asset_too_short_excluded_and_flagged(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    beats = _beats([("b1", 3.0), ("b2", 3.0)])
    assets = _assets([("b1", 10.0), ("b2", 0.5)])  # b2 too short
    _write(input_dir, beats, assets)
    plan = _plan_json(
        [{"beat_id": "b1", "asset_id": "asset_b1", "shots": [{"shot_id": "b1_s1", "in_s": 0, "out_s": 3.0, "hold_duration_s": 3.0}], "transition_out": "hard-cut", "rationale": ""}]
    )

    response = run.main(input_dir, output_dir, RUN_CONFIG, agent_call=lambda s, u: plan, thresholds=THRESHOLDS, vocab=VOCAB)

    assert response.status.value == "NEEDS_INPUT"
    reason_codes = {item.reason_code for item in response.needs_input}
    assert "asset_too_short" in reason_codes
    out = json.loads((output_dir / "edit_plan.json").read_text(encoding="utf-8"))
    assert [b["beat_id"] for b in out["beats"]] == ["b1"]


def test_over_subdivided_needs_input(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    beats = _beats([("b1", 8.0)])
    assets = _assets([("b1", 20.0)])
    _write(input_dir, beats, assets)
    shots = [
        {"shot_id": f"b1_s{i}", "in_s": i * 2.0, "out_s": i * 2.0 + 2.0, "hold_duration_s": 2.0} for i in range(4)
    ]
    plan = _plan_json([{"beat_id": "b1", "asset_id": "asset_b1", "shots": shots, "transition_out": "hard-cut", "rationale": "many moments"}])

    response = run.main(input_dir, output_dir, RUN_CONFIG, agent_call=lambda s, u: plan, thresholds=THRESHOLDS, vocab=VOCAB)

    assert response.status.value == "NEEDS_INPUT"
    assert any(item.reason_code == "over_subdivided_shots" for item in response.needs_input)
    # still written - this is a flagged proposal, not a rejected one
    assert (output_dir / "edit_plan.json").exists()


def test_repeated_dramatic_transition_needs_input(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    beats = _beats([("b1", 3.0), ("b2", 3.0), ("b3", 3.0)])
    assets = _assets([("b1", 10.0), ("b2", 10.0), ("b3", 10.0)])
    _write(input_dir, beats, assets)
    plan = _plan_json(
        [
            {"beat_id": "b1", "asset_id": "asset_b1", "shots": [{"shot_id": "b1_s1", "in_s": 0, "out_s": 3.0, "hold_duration_s": 3.0}], "transition_out": "crossfade", "rationale": "mood shift"},
            {"beat_id": "b2", "asset_id": "asset_b2", "shots": [{"shot_id": "b2_s1", "in_s": 0, "out_s": 3.0, "hold_duration_s": 3.0}], "transition_out": "crossfade", "rationale": "mood shift"},
            {"beat_id": "b3", "asset_id": "asset_b3", "shots": [{"shot_id": "b3_s1", "in_s": 0, "out_s": 3.0, "hold_duration_s": 3.0}], "transition_out": "hard-cut", "rationale": ""},
        ]
    )

    response = run.main(input_dir, output_dir, RUN_CONFIG, agent_call=lambda s, u: plan, thresholds=THRESHOLDS, vocab=VOCAB)

    assert response.status.value == "NEEDS_INPUT"
    assert any(item.reason_code == "repeated_dramatic_transition" for item in response.needs_input)


def test_runtime_drift_needs_input(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    beats = _beats([("b1", 2.0)])  # planned est. 2.0s
    assets = _assets([("b1", 10.0)])
    _write(input_dir, beats, assets)
    # hold_duration_s=4.0 vs est 2.0 -> 100% drift, way over 15% limit
    plan = _plan_json([{"beat_id": "b1", "asset_id": "asset_b1", "shots": [{"shot_id": "b1_s1", "in_s": 0, "out_s": 4.0, "hold_duration_s": 4.0}], "transition_out": "hard-cut", "rationale": ""}])

    response = run.main(input_dir, output_dir, RUN_CONFIG, agent_call=lambda s, u: plan, thresholds=THRESHOLDS, vocab=VOCAB)

    assert response.status.value == "NEEDS_INPUT"
    assert any(item.reason_code == "runtime_drift" for item in response.needs_input)


def test_invalid_transition_needs_input(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    beats = _beats([("b1", 3.0)])
    assets = _assets([("b1", 10.0)])
    _write(input_dir, beats, assets)
    plan = _plan_json([{"beat_id": "b1", "asset_id": "asset_b1", "shots": [{"shot_id": "b1_s1", "in_s": 0, "out_s": 3.0, "hold_duration_s": 3.0}], "transition_out": "star-wipe", "rationale": ""}])

    response = run.main(input_dir, output_dir, RUN_CONFIG, agent_call=lambda s, u: plan, thresholds=THRESHOLDS, vocab=VOCAB)

    assert response.status.value == "NEEDS_INPUT"
    assert response.needs_input[0].reason_code == "edit_plan_incomplete"
    assert not (output_dir / "edit_plan.json").exists()


def test_missing_input_files_fails(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    input_dir.mkdir()

    response = run.main(input_dir, output_dir, RUN_CONFIG, thresholds=THRESHOLDS, vocab=VOCAB)

    assert response.status.value == "FAILED"


def test_hold_duration_near_miss_clamped(tmp_path):
    # Regression test for the real run: a small local model produced
    # hold_duration_s=2.25 against a [2.5, 6.0] range - a 0.25s (5% of the
    # 3.5s span) near-miss that should clamp to 2.5 rather than block.
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    beats = _beats([("b1", 2.5)])  # matches the post-clamp value, so this isolates clamping from the runtime_drift check
    assets = _assets([("b1", 10.0)])
    _write(input_dir, beats, assets)
    plan = _plan_json([{"beat_id": "b1", "asset_id": "asset_b1", "shots": [{"shot_id": "b1_s1", "in_s": 0, "out_s": 3.75, "hold_duration_s": 2.25}], "transition_out": "hard-cut", "rationale": ""}])
    thresholds = {"editorial": {"min_viable_shot_length_s": 1.5, "max_runtime_drift_pct": 15, "hold_duration_clamp_tolerance_pct": 10}}
    vocab = {**VOCAB, "pacing_presets": {"standard": {"hold_duration_s": {"min": 2.5, "max": 6.0}, "max_shots_per_beat": 5}}}

    response = run.main(input_dir, output_dir, RUN_CONFIG, agent_call=lambda s, u: plan, thresholds=thresholds, vocab=vocab)

    assert response.status.value == "COMPLETE"
    assert "clamped" in response.summary
    out = json.loads((output_dir / "edit_plan.json").read_text(encoding="utf-8"))
    assert out["beats"][0]["shots"][0]["hold_duration_s"] == 2.5


def test_hold_duration_far_outside_tolerance_still_blocks(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    beats = _beats([("b1", 3.0)])
    assets = _assets([("b1", 10.0)])
    _write(input_dir, beats, assets)
    # 0.5s (standard range) is nowhere near [2.5, 6.0] even with 10% tolerance (0.35s)
    plan = _plan_json([{"beat_id": "b1", "asset_id": "asset_b1", "shots": [{"shot_id": "b1_s1", "in_s": 0, "out_s": 0.5, "hold_duration_s": 0.5}], "transition_out": "hard-cut", "rationale": ""}])
    thresholds = {"editorial": {"min_viable_shot_length_s": 1.5, "max_runtime_drift_pct": 15, "hold_duration_clamp_tolerance_pct": 10}}
    vocab = {**VOCAB, "pacing_presets": {"standard": {"hold_duration_s": {"min": 2.5, "max": 6.0}, "max_shots_per_beat": 5}}}

    response = run.main(input_dir, output_dir, RUN_CONFIG, agent_call=lambda s, u: plan, thresholds=thresholds, vocab=vocab)

    assert response.status.value == "NEEDS_INPUT"
    assert not (output_dir / "edit_plan.json").exists()


def test_omitting_agent_call_uses_default(tmp_path, monkeypatch):
    # Regression test: main() previously never defaulted agent_call to
    # _default_agent_call, so omitting it raised "'NoneType' object is not
    # callable" instead of reaching the (mocked-here) real backend. Every
    # other test passes agent_call explicitly, so none of them caught this.
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    beats = _beats([("b1", 3.0)])
    assets = _assets([("b1", 10.0)])
    _write(input_dir, beats, assets)
    plan = _plan_json([{"beat_id": "b1", "asset_id": "asset_b1", "shots": [{"shot_id": "b1_s1", "in_s": 0, "out_s": 3.0, "hold_duration_s": 3.0}], "transition_out": "hard-cut", "rationale": ""}])
    monkeypatch.setattr(run, "_default_agent_call", lambda s, u: plan)

    response = run.main(input_dir, output_dir, RUN_CONFIG, thresholds=THRESHOLDS, vocab=VOCAB)

    assert response.status.value == "COMPLETE"
