from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import requests

# See stage 01's test_run.py for why this isn't a plain "import run".
STAGE_SRC = Path(__file__).resolve().parents[1] / "src"
_spec = importlib.util.spec_from_file_location("stage05_retrieval_verification_run", STAGE_SRC / "run.py")
run = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = run
_spec.loader.exec_module(run)

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))
from shared.media import FFmpegError  # noqa: E402

THRESHOLDS = {
    "retrieval_verification": {"top_k": 3, "frames_per_candidate": 2},
    "clip_reranking": {"similarity_cutoff": 0.5, "close_score_margin": 0.05},
}


def _vec_with_similarity(score: float) -> np.ndarray:
    score = max(-1.0, min(1.0, score))
    return np.array([score, (1 - score**2) ** 0.5])


class FakeEmbedder:
    def __init__(self, score_by_candidate: dict[str, float]):
        self.score_by_candidate = score_by_candidate

    def embed_text(self, text: str) -> np.ndarray:
        return np.array([1.0, 0.0])

    def embed_image_bytes(self, image_bytes: bytes) -> np.ndarray:
        candidate_id = image_bytes.decode()
        return _vec_with_similarity(self.score_by_candidate.get(candidate_id, 0.0))


def _fake_downloader(fail_for: set[str] = frozenset()):
    def downloader(url: str, dest: Path) -> None:
        candidate_id = dest.stem
        if candidate_id in fail_for:
            raise requests.RequestException("simulated download failure")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake video bytes")

    return downloader


def _fake_frame_extractor(fail_for: set[str] = frozenset()):
    def extractor(video_path: Path, output_dir: Path, n_frames: int) -> list[Path]:
        candidate_id = video_path.stem
        if candidate_id in fail_for:
            raise FFmpegError("simulated extraction failure")
        output_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for i in range(n_frames):
            p = output_dir / f"frame_{i}.jpg"
            p.write_bytes(candidate_id.encode())
            paths.append(p)
        return paths

    return extractor


def _candidate(cid: str, sim_score: float, duration: float = 10.0) -> dict:
    return {
        "candidate_id": cid,
        "source": "pexels",
        "url": f"https://pexels.com/video/{cid}",
        "license": "Pexels License",
        "thumbnail_ref": f"https://img/{cid}.jpg",
        "download_url": f"https://videos.pexels.com/{cid}.mp4",
        "duration_s": duration,
        "creator": "Jane Doe",
        "similarity_score": sim_score,
    }


def _make_inputs(run_id: str, candidates: list[dict], route: str = "05_retrieval_verification") -> tuple[dict, dict]:
    beats = {
        "run_id": run_id,
        "scene_id": "ch1_sc1",
        "beats": [
            {
                "beat_id": "b1",
                "order": 0,
                "text_excerpt_ref": "para:1",
                "visual_description": "desc",
                "est_duration_s": 3.0,
                "mood_tags": ["quiet"],
                "no_visual_analog": False,
            }
        ],
    }
    candidates_data = {
        "run_id": run_id,
        "scene_id": "ch1_sc1",
        "candidates_by_beat": [
            {
                "beat_id": "b1",
                "search_terms": ["desc"],
                "candidates": candidates,
                "routing": {"route": route, "best_score": 0.9, "retrievable": "high" if route == "05_retrieval_verification" else "none"},
            }
        ],
    }
    return beats, candidates_data


def _write(input_dir: Path, beats: dict, candidates: dict) -> None:
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "beats.json").write_text(json.dumps(beats), encoding="utf-8")
    (input_dir / "candidates.json").write_text(json.dumps(candidates), encoding="utf-8")


def _clean_run_dir(run_id: str) -> None:
    run_dir = REPO_ROOT / "shared" / "runs" / run_id
    if run_dir.exists():
        shutil.rmtree(run_dir)


def test_auto_selects_when_clearly_ahead(tmp_path):
    run_id = "test_run_05_auto"
    _clean_run_dir(run_id)
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    candidates = [_candidate("c1", 0.9), _candidate("c2", 0.8), _candidate("c3", 0.7)]
    beats, candidates_data = _make_inputs(run_id, candidates)
    _write(input_dir, beats, candidates_data)
    embedder = FakeEmbedder({"c1": 0.9, "c2": 0.5, "c3": 0.4})

    response = run.main(
        input_dir, output_dir, {"run_id": run_id},
        downloader=_fake_downloader(), frame_extractor=_fake_frame_extractor(), embedder=embedder, thresholds=THRESHOLDS,
    )

    assert response.status.value == "COMPLETE"
    assets = json.loads((output_dir / "assets_manifest.json").read_text(encoding="utf-8"))
    assert assets["assets"][0]["asset_id"] == "c1"
    assert assets["assets"][0]["origin"] == "retrieved_verified"

    _clean_run_dir(run_id)


def test_needs_input_when_close_after_verification(tmp_path):
    run_id = "test_run_05_close"
    _clean_run_dir(run_id)
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    candidates = [_candidate("c1", 0.9), _candidate("c2", 0.8), _candidate("c3", 0.7)]
    beats, candidates_data = _make_inputs(run_id, candidates)
    _write(input_dir, beats, candidates_data)
    embedder = FakeEmbedder({"c1": 0.52, "c2": 0.50, "c3": 0.10})

    response = run.main(
        input_dir, output_dir, {"run_id": run_id},
        downloader=_fake_downloader(), frame_extractor=_fake_frame_extractor(), embedder=embedder, thresholds=THRESHOLDS,
    )

    assert response.status.value == "NEEDS_INPUT"
    assert response.needs_input[0].reason_code == "close_score_tiebreak"
    assert not (output_dir / "assets_manifest.json").exists()

    _clean_run_dir(run_id)


def test_hitl_decision_resolves_pending_beat(tmp_path):
    run_id = "test_run_05_decision"
    _clean_run_dir(run_id)
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    candidates = [_candidate("c1", 0.9), _candidate("c2", 0.8)]
    beats, candidates_data = _make_inputs(run_id, candidates)
    _write(input_dir, beats, candidates_data)
    embedder = FakeEmbedder({"c1": 0.52, "c2": 0.50})

    response = run.main(
        input_dir, output_dir, {"run_id": run_id},
        downloader=_fake_downloader(), frame_extractor=_fake_frame_extractor(), embedder=embedder, thresholds=THRESHOLDS,
        hitl_decisions={"b1": "c2"},
    )

    assert response.status.value == "COMPLETE"
    assets = json.loads((output_dir / "assets_manifest.json").read_text(encoding="utf-8"))
    assert assets["assets"][0]["asset_id"] == "c2"

    _clean_run_dir(run_id)


def test_all_candidates_fail_routes_fallback(tmp_path):
    run_id = "test_run_05_allfail"
    _clean_run_dir(run_id)
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    candidates = [_candidate("c1", 0.9), _candidate("c2", 0.8)]
    beats, candidates_data = _make_inputs(run_id, candidates)
    _write(input_dir, beats, candidates_data)
    embedder = FakeEmbedder({"c1": 0.9, "c2": 0.8})

    response = run.main(
        input_dir, output_dir, {"run_id": run_id},
        downloader=_fake_downloader(fail_for={"c1", "c2"}), frame_extractor=_fake_frame_extractor(),
        embedder=embedder, thresholds=THRESHOLDS,
    )

    assert response.status.value == "FALLBACK_ROUTED"
    assert response.fallback_routed[0].reason_code == "verification_failed"
    assert response.fallback_routed[0].item_id == "b1"

    _clean_run_dir(run_id)


def test_single_candidate_auto_selects_without_margin_check(tmp_path):
    run_id = "test_run_05_single"
    _clean_run_dir(run_id)
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    candidates = [_candidate("c1", 0.9)]
    beats, candidates_data = _make_inputs(run_id, candidates)
    _write(input_dir, beats, candidates_data)
    embedder = FakeEmbedder({"c1": 0.3})

    response = run.main(
        input_dir, output_dir, {"run_id": run_id},
        downloader=_fake_downloader(), frame_extractor=_fake_frame_extractor(), embedder=embedder, thresholds=THRESHOLDS,
    )

    assert response.status.value == "COMPLETE"
    assets = json.loads((output_dir / "assets_manifest.json").read_text(encoding="utf-8"))
    assert assets["assets"][0]["asset_id"] == "c1"

    _clean_run_dir(run_id)


def test_skips_beats_not_routed_to_05(tmp_path):
    run_id = "test_run_05_skip"
    _clean_run_dir(run_id)
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    candidates = [_candidate("c1", 0.9)]
    beats, candidates_data = _make_inputs(run_id, candidates, route="06_fallback_generation")
    _write(input_dir, beats, candidates_data)
    embedder = FakeEmbedder({"c1": 0.9})

    response = run.main(
        input_dir, output_dir, {"run_id": run_id},
        downloader=_fake_downloader(), frame_extractor=_fake_frame_extractor(), embedder=embedder, thresholds=THRESHOLDS,
    )

    assert response.status.value == "COMPLETE"
    assert not (output_dir / "assets_manifest.json").exists()

    _clean_run_dir(run_id)


def test_missing_input_files_fails(tmp_path):
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    input_dir.mkdir()

    response = run.main(input_dir, output_dir, {"run_id": "test_run_05_missing"}, thresholds=THRESHOLDS)

    assert response.status.value == "FAILED"


def test_assets_per_beat_retains_ranked_alternates(tmp_path):
    run_id = "test_run_05_multiangle"
    _clean_run_dir(run_id)
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    candidates = [_candidate("c1", 0.9), _candidate("c2", 0.8), _candidate("c3", 0.7)]
    beats, candidates_data = _make_inputs(run_id, candidates)
    _write(input_dir, beats, candidates_data)
    embedder = FakeEmbedder({"c1": 0.9, "c2": 0.5, "c3": 0.4})
    thresholds = {**THRESHOLDS, "retrieval_verification": {**THRESHOLDS["retrieval_verification"], "assets_per_beat": 2}}

    response = run.main(
        input_dir, output_dir, {"run_id": run_id},
        downloader=_fake_downloader(), frame_extractor=_fake_frame_extractor(), embedder=embedder, thresholds=thresholds,
    )

    assert response.status.value == "COMPLETE"
    assets = json.loads((output_dir / "assets_manifest.json").read_text(encoding="utf-8"))["assets"]
    assert [a["asset_id"] for a in assets] == ["c1", "c2"]
    assert [a["rank"] for a in assets] == [1, 2]
    assert all(a["beat_id"] == "b1" for a in assets)

    _clean_run_dir(run_id)


def test_assets_per_beat_default_keeps_only_winner(tmp_path):
    # THRESHOLDS has no assets_per_beat key - .get(..., 1) default must
    # preserve the pre-multi-angle behavior exactly (regression guard).
    run_id = "test_run_05_default_single"
    _clean_run_dir(run_id)
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    candidates = [_candidate("c1", 0.9), _candidate("c2", 0.8)]
    beats, candidates_data = _make_inputs(run_id, candidates)
    _write(input_dir, beats, candidates_data)
    embedder = FakeEmbedder({"c1": 0.9, "c2": 0.3})

    response = run.main(
        input_dir, output_dir, {"run_id": run_id},
        downloader=_fake_downloader(), frame_extractor=_fake_frame_extractor(), embedder=embedder, thresholds=THRESHOLDS,
    )

    assert response.status.value == "COMPLETE"
    assets = json.loads((output_dir / "assets_manifest.json").read_text(encoding="utf-8"))["assets"]
    assert len(assets) == 1
    assert assets[0]["rank"] == 1

    _clean_run_dir(run_id)


def test_assets_per_beat_with_hitl_decision_ranks_chosen_first(tmp_path):
    run_id = "test_run_05_multiangle_hitl"
    _clean_run_dir(run_id)
    input_dir, output_dir = tmp_path / "in", tmp_path / "out"
    candidates = [_candidate("c1", 0.9), _candidate("c2", 0.8)]
    beats, candidates_data = _make_inputs(run_id, candidates)
    _write(input_dir, beats, candidates_data)
    embedder = FakeEmbedder({"c1": 0.52, "c2": 0.50})
    thresholds = {**THRESHOLDS, "retrieval_verification": {**THRESHOLDS["retrieval_verification"], "assets_per_beat": 2}}

    response = run.main(
        input_dir, output_dir, {"run_id": run_id},
        downloader=_fake_downloader(), frame_extractor=_fake_frame_extractor(), embedder=embedder, thresholds=thresholds,
        hitl_decisions={"b1": "c2"},
    )

    assert response.status.value == "COMPLETE"
    assets = json.loads((output_dir / "assets_manifest.json").read_text(encoding="utf-8"))["assets"]
    assert [a["asset_id"] for a in assets] == ["c2", "c1"]
    assert [a["rank"] for a in assets] == [1, 2]

    _clean_run_dir(run_id)
