"""Regression tests for the Coordinator's resolve_music_stage() retry loop.

The orchestration these cover used to live in run_full_novel.py; it now lives in
the real Coordinator (stages/00_coordinator/src/run.py), so this test loads that
module directly (its monkeypatch targets - stage_main, _build_music_source,
log_event, _default_downloader_invoke - must be attributes of the module that
defines resolve_music_stage/download_scene_queries). Covers the stale-track_ref
bug found for real 2026-07-23 (see ARCHITECTURE.md/DECISIONS_LOG.md): Stage 09's
live music search is re-run fresh on every invocation, so a track_ref recorded
as this run's decision for a cue_id on one attempt can legitimately be absent
from a later attempt's freshly re-searched candidate list for that same cue_id,
and Stage 09 correctly returns FAILED rather than guessing.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _load_coordinator():
    path = REPO_ROOT / "stages" / "00_coordinator" / "src" / "run.py"
    spec = importlib.util.spec_from_file_location("coordinator_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


orch = _load_coordinator()
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


def test_download_scene_queries_dedups_and_issues_unique(tmp_path):
    """download_scene_queries (2026-07-24) invokes the downloader once per
    UNIQUE beat query (many beats legitimately share one, e.g. a multi-beat
    speech), populating the shared outputs/ pool; the scene later scores the
    WHOLE pool, so there's no fragile per-scene snapshot. Real downloader is
    never run; `invoke` is injected."""
    invoked: list[str] = []
    beats = [
        {"beat_id": "b1", "visual_description": "x", "search_query": "pig speaking barn"},
        {"beat_id": "b2", "visual_description": "y", "search_query": "pig speaking barn"},  # dup
        {"beat_id": "b3", "visual_description": "z", "search_query": "animals fleeing barn"},
    ]
    queries = orch.download_scene_queries(beats, "sc1", invoke=lambda q: invoked.append(q))

    assert queries == ["pig speaking barn", "animals fleeing barn"]
    assert invoked == ["pig speaking barn", "animals fleeing barn"]  # dup issued only once


def test_download_scene_queries_falls_back_to_extracted_terms(monkeypatch):
    """A beat with no search_query drives the downloader with mechanically
    extracted keywords from its visual_description (same fallback Stage 03 used)."""
    queries: list[str] = []
    monkeypatch.setattr(orch, "_default_downloader_invoke", lambda q: queries.append(q))

    beats = [{"beat_id": "b1", "visual_description": "A woman climbs a narrow attic staircase."}]
    orch.download_scene_queries(beats, "sc1")  # default invoke -> monkeypatched

    assert len(queries) == 1
    assert queries[0].strip()  # non-empty derived query
    assert "attic" in queries[0]


def test_download_scene_queries_survives_a_failed_query(monkeypatch):
    """One query raising (e.g. the downloader errored) is logged and skipped,
    not fatal - the scene's other queries still run."""
    events: list[dict] = []
    monkeypatch.setattr(orch, "log_event", lambda e: events.append(e))

    def flaky_invoke(query: str) -> None:
        if query == "boom":
            raise RuntimeError("downloader failed")

    beats = [
        {"beat_id": "b1", "visual_description": "x", "search_query": "boom"},
        {"beat_id": "b2", "visual_description": "y", "search_query": "good clip"},
    ]
    queries = orch.download_scene_queries(beats, "sc1", invoke=flaky_invoke)

    assert queries == ["boom", "good clip"]  # both attempted
    assert any(e.get("event") == "DOWNLOADER_QUERY_FAILED" for e in events)


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
