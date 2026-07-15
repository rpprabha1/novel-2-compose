import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import requests

# See stage 01's test_run.py for why this isn't a plain "import run".
STAGE_SRC = Path(__file__).resolve().parents[1] / "src"
_spec = importlib.util.spec_from_file_location("stage04_clip_reranking_run", STAGE_SRC / "run.py")
run = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = run
_spec.loader.exec_module(run)

THRESHOLDS = {"clip_reranking": {"similarity_cutoff": 0.5, "close_score_margin": 0.05}}
BASE_TEXT_VEC = np.array([1.0, 0.0])


def _vec_with_similarity(score: float) -> np.ndarray:
    score = max(-1.0, min(1.0, score))
    return np.array([score, (1 - score**2) ** 0.5])


class FakeEmbedder:
    def __init__(self, image_scores: dict[str, float], fail_urls: set[str] | None = None):
        self.image_scores = image_scores
        self.fail_urls = fail_urls or set()

    def embed_text(self, text: str) -> np.ndarray:
        return BASE_TEXT_VEC

    def embed_image_url(self, url: str) -> np.ndarray:
        if url in self.fail_urls:
            raise requests.RequestException("simulated failure")
        return _vec_with_similarity(self.image_scores[url])


def _beats(beat_ids: list[str]) -> dict:
    return {
        "run_id": "test_run_04",
        "scene_id": "ch1_sc1",
        "beats": [
            {
                "beat_id": bid,
                "order": i,
                "text_excerpt_ref": f"para:{i + 1}",
                "visual_description": "desc",
                "est_duration_s": 3.0,
                "mood_tags": ["quiet"],
                "no_visual_analog": False,
            }
            for i, bid in enumerate(beat_ids)
        ],
    }


def _candidates(beat_id: str, candidate_urls: list[str]) -> dict:
    return {
        "run_id": "test_run_04",
        "scene_id": "ch1_sc1",
        "candidates_by_beat": [
            {
                "beat_id": beat_id,
                "search_terms": ["desc"],
                "candidates": [
                    {
                        "candidate_id": f"pexels_{i}",
                        "source": "pexels",
                        "url": f"https://pexels.com/video/{i}",
                        "license": "Pexels License",
                        "thumbnail_ref": url,
                    }
                    for i, url in enumerate(candidate_urls)
                ],
            }
        ],
    }


def _write(input_dir: Path, beats: dict, candidates: dict) -> None:
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "beats.json").write_text(json.dumps(beats), encoding="utf-8")
    (input_dir / "candidates.json").write_text(json.dumps(candidates), encoding="utf-8")


def test_routes_high_above_cutoff_plus_margin(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    beats = _beats(["b1"])
    candidates = _candidates("b1", ["https://img/high"])
    _write(input_dir, beats, candidates)
    embedder = FakeEmbedder({"https://img/high": 0.9})

    response = run.main(input_dir, output_dir, {"run_id": "test_run_04"}, embedder=embedder, thresholds=THRESHOLDS)

    assert response.status.value == "COMPLETE"
    out = json.loads((output_dir / "candidates.json").read_text(encoding="utf-8"))
    routing = out["candidates_by_beat"][0]["routing"]
    assert routing["route"] == "05_retrieval_verification"
    assert routing["retrievable"] == "high"


def test_routes_low_within_close_margin(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    beats = _beats(["b1"])
    candidates = _candidates("b1", ["https://img/close"])
    _write(input_dir, beats, candidates)
    embedder = FakeEmbedder({"https://img/close": 0.52})  # within [0.45, 0.55]

    response = run.main(input_dir, output_dir, {"run_id": "test_run_04"}, embedder=embedder, thresholds=THRESHOLDS)

    out = json.loads((output_dir / "candidates.json").read_text(encoding="utf-8"))
    routing = out["candidates_by_beat"][0]["routing"]
    assert routing["route"] == "05_retrieval_verification"
    assert routing["retrievable"] == "low"


def test_routes_fallback_below_cutoff_minus_margin(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    beats = _beats(["b1"])
    candidates = _candidates("b1", ["https://img/low"])
    _write(input_dir, beats, candidates)
    embedder = FakeEmbedder({"https://img/low": 0.1})

    response = run.main(input_dir, output_dir, {"run_id": "test_run_04"}, embedder=embedder, thresholds=THRESHOLDS)

    out = json.loads((output_dir / "candidates.json").read_text(encoding="utf-8"))
    routing = out["candidates_by_beat"][0]["routing"]
    assert routing["route"] == "06_fallback_generation"
    assert routing["retrievable"] == "none"


def test_zero_candidates_routes_fallback(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    beats = _beats(["b1"])
    candidates = _candidates("b1", [])
    _write(input_dir, beats, candidates)
    embedder = FakeEmbedder({})

    response = run.main(input_dir, output_dir, {"run_id": "test_run_04"}, embedder=embedder, thresholds=THRESHOLDS)

    assert response.status.value == "COMPLETE"
    out = json.loads((output_dir / "candidates.json").read_text(encoding="utf-8"))
    routing = out["candidates_by_beat"][0]["routing"]
    assert routing["route"] == "06_fallback_generation"
    assert routing["best_score"] == -1.0


def test_image_failure_scored_worst_case_not_crash(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    beats = _beats(["b1"])
    candidates = _candidates("b1", ["https://img/broken", "https://img/good"])
    _write(input_dir, beats, candidates)
    embedder = FakeEmbedder({"https://img/good": 0.9}, fail_urls={"https://img/broken"})

    response = run.main(input_dir, output_dir, {"run_id": "test_run_04"}, embedder=embedder, thresholds=THRESHOLDS)

    assert response.status.value == "COMPLETE"
    assert "1 thumbnail fetch/decode failure" in response.summary
    out = json.loads((output_dir / "candidates.json").read_text(encoding="utf-8"))
    scores = {c["candidate_id"]: c["similarity_score"] for c in out["candidates_by_beat"][0]["candidates"]}
    assert scores["pexels_0"] == -1.0
    assert scores["pexels_1"] == 0.9


def test_missing_input_files_fails(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    input_dir.mkdir()

    response = run.main(input_dir, output_dir, {"run_id": "test_run_04"}, embedder=FakeEmbedder({}), thresholds=THRESHOLDS)

    assert response.status.value == "FAILED"


def test_unknown_beat_id_in_candidates_fails(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    beats = _beats(["b1"])
    candidates = _candidates("b_unknown", ["https://img/x"])
    _write(input_dir, beats, candidates)

    response = run.main(
        input_dir, output_dir, {"run_id": "test_run_04"}, embedder=FakeEmbedder({"https://img/x": 0.5}), thresholds=THRESHOLDS
    )

    assert response.status.value == "FAILED"
