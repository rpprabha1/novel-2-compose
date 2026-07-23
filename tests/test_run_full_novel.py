"""Regression tests for run_full_novel.py's resolve_music_stage() retry loop.

Ad-hoc orchestrator script (repo root, not a stage's own src/ - see its own
module docstring), so this lives in a root-level tests/ dir rather than a
stage's tests/. Covers the stale-track_ref bug found for real 2026-07-23 (see
ARCHITECTURE.md/DECISIONS_LOG.md): Stage 09's live music search is re-run
fresh on every invocation, so a track_ref recorded as this run's decision for
a cue_id on one attempt can legitimately be absent from a later attempt's
freshly re-searched candidate list for that same cue_id, and Stage 09
correctly returns FAILED rather than guessing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import run_full_novel as orch  # noqa: E402
from shared.envelopes import ErrorInfo, NeedsInputItem, StageResponse, StageStatus  # noqa: E402


def _needs_input_track_selection(track_refs: list[str]) -> StageResponse:
    options = [f"{ref} (jamendo, CC-BY, https://example.com/{ref})" for ref in track_refs]
    return StageResponse(
        envelope_id="", run_id="test", stage="09_audio_production", status=StageStatus.NEEDS_INPUT,
        needs_input=[NeedsInputItem(reason_code="track_selection", question="Cue cue001 (['quiet']): pick a track.", options=options)],
    )


def _failed_stale_ref(track_ref: str) -> StageResponse:
    return StageResponse(
        envelope_id="", run_id="test", stage="09_audio_production", status=StageStatus.FAILED,
        error=ErrorInfo(message=f"hitl_decisions references track_ref {track_ref!r} not among cue 'cue001''s candidates"),
    )


def _complete() -> StageResponse:
    return StageResponse(envelope_id="", run_id="test", stage="09_audio_production", status=StageStatus.COMPLETE, summary="ok")


def test_resolve_music_stage_recovers_from_stale_track_ref(monkeypatch, tmp_path):
    """The exact real sequence: attempt 1 asks to pick a track; attempt 2
    (with that choice as hitl_decisions) fails because the live search
    re-ran and returned different candidates the second time around (no
    music_cue_intent.json is written by this fake, so the cue-sheet-freeze
    root-cause fix never engages - this test exercises the belt-and-
    suspenders drop-stale-and-retry safety net specifically); the fix must
    drop the stale decision and retry rather than propagate the FAILED
    straight through."""
    calls: list[dict] = []

    def fake_main09(input_dir, output_dir, run_config, **kwargs):
        calls.append(kwargs)
        hitl = kwargs.get("hitl_decisions") or {}
        if not hitl:
            # First ask offers the track that will go stale; a second ask
            # (after the stale entry is dropped) simulates the cue-sheet/
            # live search having moved on to different candidates.
            offered = "jamendo_682351" if len(calls) == 1 else "jamendo_999999"
            return _needs_input_track_selection([offered])
        if hitl.get("cue001") == "jamendo_682351":
            return _failed_stale_ref("jamendo_682351")
        return _complete()

    monkeypatch.setattr(orch, "stage_main", lambda n: fake_main09)
    monkeypatch.setattr(orch, "_build_music_source", lambda: (object(), None))

    decisions: list = []
    resp = orch.resolve_music_stage({"run_id": "test"}, tmp_path, decisions)

    assert resp.status == StageStatus.COMPLETE
    assert len(calls) == 4  # initial ask, stale attempt, re-ask, recovered retry
    assert any(d["decision_point"] == "stale_track_selection" for d in decisions)


def test_caching_music_source_memoizes_identical_queries():
    """Direct unit test of the root-cause fix's caching layer: a live
    MusicSource is not guaranteed to return identical results across two
    separate real calls with the same mood_tags - this wrapper makes repeat
    queries within one resolution sequence deterministic."""
    calls: list[tuple] = []

    class _Inner:
        def search(self, mood_tags, max_results=3):
            calls.append((tuple(mood_tags), max_results))
            return [f"candidate-{len(calls)}"]

    cached = orch._CachingMusicSource(_Inner())
    first = cached.search(["quiet", "tense"], 3)
    second = cached.search(["quiet", "tense"], 3)
    third = cached.search(["ominous"], 3)

    assert first == second
    assert third != first
    assert len(calls) == 2  # only 2 distinct underlying searches actually ran


def test_resolve_music_stage_freezes_cue_sheet_once_valid(monkeypatch, tmp_path):
    """Once a real attempt's cue-sheet passes validation
    (music_cue_intent.json written), every later call in the same
    resolution sequence must receive a frozen agent_call replaying that
    exact cue list, rather than a fresh LLM regeneration that could drift to
    different cue_ids/mood_tags mid-resolution."""
    calls: list[dict] = []
    frozen_cues_seen: list = []
    real_cues = [
        {
            "cue_id": "cue001",
            "start_beat_id": "b001",
            "end_beat_id": "b002",
            "mood_tags": ["quiet"],
            "target_intensity": 0.5,
            "rationale": "r",
        }
    ]

    def fake_main09(input_dir, output_dir, run_config, **kwargs):
        calls.append(kwargs)
        if "agent_call" in kwargs:
            frozen_cues_seen.append(json.loads(kwargs["agent_call"]("sys", "user"))["cues"])
        if len(calls) == 1:
            (output_dir / "music_cue_intent.json").write_text(json.dumps({"cues": real_cues}), encoding="utf-8")
            return _needs_input_track_selection(["jamendo_1"])
        return _complete()

    monkeypatch.setattr(orch, "stage_main", lambda n: fake_main09)
    monkeypatch.setattr(orch, "_build_music_source", lambda: (object(), None))

    resp = orch.resolve_music_stage({"run_id": "test"}, tmp_path, [])

    assert resp.status == StageStatus.COMPLETE
    assert len(calls) == 2
    assert "agent_call" not in calls[0]
    assert "agent_call" in calls[1]
    assert frozen_cues_seen == [real_cues]


def test_resolve_music_stage_still_handles_cues_incomplete(monkeypatch, tmp_path):
    """Existing behavior (pre-dating this fix) must survive unchanged: a
    structurally-invalid cue-sheet is retried by regenerating fresh, not by
    touching hitl_decisions at all."""
    calls: list[dict] = []

    def fake_main09(input_dir, output_dir, run_config, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return StageResponse(
                envelope_id="", run_id="test", stage="09_audio_production", status=StageStatus.NEEDS_INPUT,
                needs_input=[NeedsInputItem(reason_code="cues_incomplete", question="cue002: invalid range", options=[])],
            )
        return _complete()

    monkeypatch.setattr(orch, "stage_main", lambda n: fake_main09)
    monkeypatch.setattr(orch, "_build_music_source", lambda: (object(), None))

    resp = orch.resolve_music_stage({"run_id": "test"}, tmp_path, [])

    assert resp.status == StageStatus.COMPLETE
    assert len(calls) == 2
