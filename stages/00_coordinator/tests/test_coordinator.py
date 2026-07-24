"""Synthetic-fixture tests for the Coordinator CODE core (CLAUDE.md section 5).

No real stage, model, or downloader is exercised - fake stage `main()` callables
stand in, and the coordinator_log is redirected into tmp_path so tests never
touch shared/runs/. Covers: envelope construction + validation, response-wrapper
validation, output-payload validation against the stage's expected_output_schema,
gate enforcement, and append-only envelope/response logging.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import jsonschema
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_coordinator():
    path = REPO_ROOT / "stages" / "00_coordinator" / "src" / "run.py"
    spec = importlib.util.spec_from_file_location("coordinator_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


coord_mod = _load_coordinator()
from shared.envelopes import StageResponse, StageStatus  # noqa: E402

_VALID_BEATS = {
    "run_id": "t",
    "scene_id": "s",
    "beats": [
        {
            "beat_id": "b1",
            "order": 0,
            "text_excerpt_ref": "para:1",
            "visual_description": "a barn at dawn",
            "est_duration_s": 2.0,
            "mood_tags": ["quiet"],
        }
    ],
}


def _make_coord(tmp_path: Path):
    coord = coord_mod.Coordinator({"run_id": "test_run"})
    coord.log_path = tmp_path / "coordinator_log.jsonl"  # keep tests out of shared/runs/
    return coord


def _log_events(coord) -> list[dict]:
    if not coord.log_path.exists():
        return []
    return [json.loads(line) for line in coord.log_path.read_text(encoding="utf-8").splitlines()]


def test_begin_builds_and_logs_a_valid_envelope(tmp_path):
    coord = _make_coord(tmp_path)
    env = coord.begin("02_beat_extraction", ["inputs/scene.txt"])

    assert env.stage == "02_beat_extraction"
    assert env.expected_output_schema == "beats.schema.json"  # from STAGE_CONTRACTS
    assert env.run_config_ref  # non-empty
    events = _log_events(coord)
    assert [e["event"] for e in events] == ["ENVELOPE"]
    assert events[0]["envelope_id"] == env.envelope_id


def test_invoke_validates_payload_and_logs_envelope_then_response(tmp_path):
    coord = _make_coord(tmp_path)
    in_dir, out_dir = tmp_path / "in", tmp_path / "out"
    out_dir.mkdir(parents=True)

    def fake_main(input_dir, output_dir, run_config, **kwargs):
        (output_dir / "beats.json").write_text(json.dumps(_VALID_BEATS), encoding="utf-8")
        return StageResponse(envelope_id="", run_id="test_run", stage="02_beat_extraction",
                             status=StageStatus.COMPLETE, summary="ok")

    resp = coord.invoke("02_beat_extraction", fake_main, in_dir, out_dir, input_manifest=["inputs/scene.txt"])

    assert resp.status == StageStatus.COMPLETE
    assert resp.envelope_id  # stamped by the coordinator
    events = _log_events(coord)
    assert [e["event"] for e in events] == ["ENVELOPE", "RESPONSE"]
    # Envelope and response share the same id (one round trip).
    assert events[0]["envelope_id"] == events[1]["envelope_id"] == resp.envelope_id


def test_invoke_rejects_a_schema_invalid_output_payload(tmp_path):
    coord = _make_coord(tmp_path)
    in_dir, out_dir = tmp_path / "in", tmp_path / "out"
    out_dir.mkdir(parents=True)

    def bad_main(input_dir, output_dir, run_config, **kwargs):
        (output_dir / "beats.json").write_text(json.dumps({"run_id": "t"}), encoding="utf-8")  # missing scene_id/beats
        return StageResponse(envelope_id="", run_id="test_run", stage="02_beat_extraction",
                             status=StageStatus.COMPLETE, summary="ok")

    with pytest.raises(jsonschema.ValidationError):
        coord.invoke("02_beat_extraction", bad_main, in_dir, out_dir, input_manifest=["inputs/scene.txt"])


def test_accept_rejects_a_malformed_response_wrapper(tmp_path):
    coord = _make_coord(tmp_path)
    env = coord.begin("02_beat_extraction", ["inputs/scene.txt"])
    bad = StageResponse(envelope_id="", run_id="test_run", stage="02_beat_extraction", status="NOT_A_STATUS")

    with pytest.raises(jsonschema.ValidationError):
        coord.accept(env, bad, tmp_path)


def test_wrapper_only_stage_skips_payload_validation(tmp_path):
    """A media stage (11) has no JSON payload contract - only its response wrapper
    is validated, and no output file is required for the invoke to succeed."""
    coord = _make_coord(tmp_path)
    in_dir, out_dir = tmp_path / "in", tmp_path / "out"
    out_dir.mkdir(parents=True)

    def fake_main(input_dir, output_dir, run_config, **kwargs):
        return StageResponse(envelope_id="", run_id="test_run", stage="11_assembly_render",
                             status=StageStatus.COMPLETE, summary="rendered")

    env = coord.begin("11_assembly_render", ["inputs/timeline.json"])
    assert env.expected_output_schema == "stage_response.schema.json"  # sentinel for no-payload stages
    resp = coord.accept(env, fake_main(in_dir, out_dir, {}), out_dir)
    assert resp.status == StageStatus.COMPLETE


def test_frontend_aggregates_film_scenes_into_one_manifest(tmp_path, monkeypatch):
    """Opt-in front-end (02_1 screenplay -> 02_2 scene extraction) with injected
    agent calls (no live model): one source scene expanding into two film scenes
    produces a single combined run-level scenes_manifest. Cleans up its
    shared/runs/<run_id> artifacts afterward."""
    import shutil

    monkeypatch.setattr(coord_mod, "log_event", lambda e: None)  # don't touch the real run's progress log
    run_id = "coord_frontend_selftest"
    coord = coord_mod.Coordinator({"run_id": run_id})
    coord.log_path = tmp_path / "coordinator_log.jsonl"

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "ch1_sc1.txt").write_text("The animals gather.\n\nA lantern moves.", encoding="utf-8")

    screenplay = json.dumps({"elements": [
        {"type": "slugline", "text": "INT. BARN - NIGHT"},
        {"type": "action", "text": "The animals gather."},
    ]})
    segmentation = json.dumps({"scenes": [
        {"heading": "INT. BARN - NIGHT", "summary": "gather", "text": "The animals gather in the barn."},
        {"heading": "EXT. FARM - LATER", "summary": "lantern", "text": "A lantern moves across the yard."},
    ]})

    try:
        combined, scenes_dir = coord_mod.run_frontend(
            coord, {"run_id": run_id}, [{"scene_id": "ch1_sc1", "order": 0}], src_dir,
            screenplay_agent_call=lambda s, u: screenplay,
            scene_agent_call=lambda s, u: segmentation,
        )
        assert [s["scene_id"] for s in combined] == ["ch1_sc1_p1", "ch1_sc1_p2"]
        assert (scenes_dir / "ch1_sc1_p1.txt").read_text(encoding="utf-8").strip()
        manifest = json.loads((scenes_dir / "scenes_manifest.json").read_text(encoding="utf-8"))
        assert len(manifest["scenes"]) == 2
        assert manifest["scenes"][0]["file_ref"] == "frontend/scenes/ch1_sc1_p1.txt"
    finally:
        shutil.rmtree(REPO_ROOT / "shared" / "runs" / run_id, ignore_errors=True)


def test_gate_enforcement_before_and_after_approval(tmp_path):
    coord = _make_coord(tmp_path)
    gate_dir = tmp_path / "stage10_out"
    gate_dir.mkdir()

    assert coord.require_approval(gate_dir, "10_human_review_gate") is False
    coord.approve(gate_dir, "# APPROVED\n")
    assert coord.require_approval(gate_dir, "10_human_review_gate") is True
    # Both gate checks were logged.
    assert [e["event"] for e in _log_events(coord)].count("GATE_CHECK") == 2
