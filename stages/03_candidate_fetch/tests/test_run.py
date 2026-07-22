from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import requests

# See stage 01's test_run.py for why this isn't a plain "import run".
STAGE_SRC = Path(__file__).resolve().parents[1] / "src"
_spec = importlib.util.spec_from_file_location("stage03_candidate_fetch_run", STAGE_SRC / "run.py")
run = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = run
_spec.loader.exec_module(run)

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))
from shared.sources import FootageCandidate  # noqa: E402

BASE_RUN_CONFIG = {"run_id": "test_run_03"}

BEATS_JSON = {
    "run_id": "test_run_03",
    "scene_id": "ch1_sc1",
    "beats": [
        {
            "beat_id": "ch1_sc1_b001",
            "order": 0,
            "text_excerpt_ref": "para:1",
            "visual_description": "A woman climbs a narrow attic staircase, dust motes drifting through light.",
            "est_duration_s": 4.0,
            "mood_tags": ["quiet"],
            "no_visual_analog": False,
        },
        {
            "beat_id": "ch1_sc1_b002",
            "order": 1,
            "text_excerpt_ref": "para:2",
            "visual_description": "She kneels and opens an old trunk, finding photographs and a letter inside.",
            "est_duration_s": 4.5,
            "mood_tags": ["quiet"],
            "no_visual_analog": False,
        },
    ],
}


class FakeSource:
    def __init__(self, name: str, canned: list[FootageCandidate]):
        self.name = name
        self.canned = canned
        self.calls: list[str] = []

    def search(self, query: str, max_results: int):
        self.calls.append(query)
        return self.canned[:max_results]


class FailingSource:
    name = "failing"

    def search(self, query: str, max_results: int):
        raise requests.RequestException("boom")


def _write_beats(input_dir: Path, data: dict = BEATS_JSON) -> None:
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "beats.json").write_text(json.dumps(data), encoding="utf-8")


def _clean_run_dir(run_id: str) -> Path:
    run_dir = REPO_ROOT / "shared" / "runs" / run_id
    import shutil

    if run_dir.exists():
        shutil.rmtree(run_dir)
    return run_dir


def test_extract_search_terms_basic():
    terms = run.extract_search_terms("A woman climbs a narrow attic staircase, dust motes drifting through the light.")
    assert "woman" in terms
    assert "attic" in terms
    assert " a " not in f" {terms} "  # stopword dropped
    assert "the" not in terms.split()


def test_main_complete_with_mocked_sources(tmp_path):
    run_id = "test_run_03_complete"
    _clean_run_dir(run_id)
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    beats = json.loads(json.dumps(BEATS_JSON))
    beats["run_id"] = run_id
    _write_beats(input_dir, beats)

    fake = FakeSource(
        "pexels",
        [
            FootageCandidate(
                candidate_id="pexels_1",
                source="pexels",
                url="https://pexels.com/video/1",
                license="Pexels License",
                thumbnail_ref="https://images.pexels.com/1.jpg",
                duration_s=10.0,
                creator="Jane Doe",
            )
        ],
    )

    response = run.main(input_dir, output_dir, {"run_id": run_id}, sources={"pexels": fake}, max_results=5)

    assert response.status.value == "COMPLETE"
    candidates = json.loads((output_dir / "candidates.json").read_text(encoding="utf-8"))
    assert len(candidates["candidates_by_beat"]) == 2
    assert candidates["candidates_by_beat"][0]["candidates"][0]["candidate_id"] == "pexels_1"

    manifest = json.loads((REPO_ROOT / "shared" / "runs" / run_id / "manifest.json").read_text(encoding="utf-8"))
    # Same candidate returned for both beats (same fake source, same canned list) -
    # manifest should be de-duplicated by entry_id, not doubled.
    assert len(manifest["entries"]) == 1
    assert manifest["entries"][0]["entry_id"] == "pexels_1"

    _clean_run_dir(run_id)


def test_search_term_overrides_used_instead_of_mechanical_extraction(tmp_path):
    # 2026-07-18: search_term_overrides lets a human supply a better query
    # for specific beats (e.g. ones that scored too low in Stage 04 with
    # the mechanical keyword-stripped query) without touching the beat's
    # own visual_description, which other stages also rely on.
    run_id = "test_run_03_override"
    _clean_run_dir(run_id)
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    beats = json.loads(json.dumps(BEATS_JSON))
    beats["run_id"] = run_id
    _write_beats(input_dir, beats)
    fake = FakeSource("pexels", [])

    response = run.main(
        input_dir, output_dir, {"run_id": run_id},
        sources={"pexels": fake}, max_results=5,
        search_term_overrides={"ch1_sc1_b002": "woman opening old trunk vintage photographs"},
    )

    assert response.status.value == "COMPLETE"
    candidates = json.loads((output_dir / "candidates.json").read_text(encoding="utf-8"))
    by_id = {b["beat_id"]: b for b in candidates["candidates_by_beat"]}
    assert by_id["ch1_sc1_b002"]["search_terms"] == ["woman opening old trunk vintage photographs"]
    # b001 had no override - still uses the mechanical extraction
    assert by_id["ch1_sc1_b001"]["search_terms"] != ["woman opening old trunk vintage photographs"]
    assert "attic" in by_id["ch1_sc1_b001"]["search_terms"][0]

    _clean_run_dir(run_id)


def test_main_caches_repeated_queries(tmp_path):
    run_id = "test_run_03_cache"
    _clean_run_dir(run_id)
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    # Two beats with identical visual_description -> identical extracted query.
    beats = {
        "run_id": run_id,
        "scene_id": "ch1_sc1",
        "beats": [
            {**BEATS_JSON["beats"][0], "beat_id": "b_a"},
            {**BEATS_JSON["beats"][0], "beat_id": "b_b"},
        ],
    }
    _write_beats(input_dir, beats)
    fake = FakeSource("pexels", [])

    run.main(input_dir, output_dir, {"run_id": run_id}, sources={"pexels": fake}, max_results=5)

    assert len(fake.calls) == 1  # second beat's identical query hit the cache

    _clean_run_dir(run_id)


def test_main_source_failure_skips_gracefully(tmp_path):
    run_id = "test_run_03_fail_source"
    _clean_run_dir(run_id)
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    beats = json.loads(json.dumps(BEATS_JSON))
    beats["run_id"] = run_id
    _write_beats(input_dir, beats)

    response = run.main(
        input_dir, output_dir, {"run_id": run_id}, sources={"failing": FailingSource()}, max_results=5
    )

    assert response.status.value == "COMPLETE"
    assert "1 API call(s) failed" in response.summary or "2 API call(s) failed" in response.summary
    candidates = json.loads((output_dir / "candidates.json").read_text(encoding="utf-8"))
    assert all(b["candidates"] == [] for b in candidates["candidates_by_beat"])

    _clean_run_dir(run_id)


def test_main_missing_beats_file_fails(tmp_path):
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    output_dir = tmp_path / "outputs"

    response = run.main(input_dir, output_dir, BASE_RUN_CONFIG, sources={"pexels": FakeSource("pexels", [])})

    assert response.status.value == "FAILED"


def test_main_no_sources_needs_input(tmp_path):
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    _write_beats(input_dir)

    response = run.main(input_dir, output_dir, BASE_RUN_CONFIG, sources={})

    assert response.status.value == "NEEDS_INPUT"
    assert response.needs_input[0].reason_code == "no_sources_configured"
