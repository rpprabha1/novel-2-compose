"""Stage 04: clip_reranking.

Computes CLIP cosine similarity between each candidate's thumbnail and its
beat's visual_description, then routes each beat to 05 (verification) or 06
(fallback) per config/thresholds.yaml. Deterministic math - CLAUDE.md
classifies this stage CODE, not agent work.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Protocol

import numpy as np
import requests
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from shared.embeddings import EmbeddingCache, cosine_similarity  # noqa: E402
from shared.envelopes import ErrorInfo, StageResponse, StageStatus, validate_against_schema  # noqa: E402

STAGE_NAME = "04_clip_reranking"


class Embedder(Protocol):
    def embed_text(self, text: str) -> np.ndarray: ...
    def embed_image_url(self, url: str) -> np.ndarray: ...


def _default_thresholds() -> dict:
    return yaml.safe_load((REPO_ROOT / "config" / "thresholds.yaml").read_text(encoding="utf-8"))


def _default_embedder(run_id: str) -> EmbeddingCache:
    cfg = yaml.safe_load((REPO_ROOT / "config" / "embeddings.yaml").read_text(encoding="utf-8"))
    cache_dir = REPO_ROOT / "shared" / "runs" / run_id / "cache" / "embeddings"
    return EmbeddingCache(model_name=cfg["clip"]["model"], device=cfg["clip"]["device"], cache_dir=cache_dir)


def main(
    input_dir: Path,
    output_dir: Path,
    run_config: dict,
    embedder: Embedder | None = None,
    thresholds: dict | None = None,
) -> StageResponse:
    run_id = run_config["run_id"]
    beats_path = input_dir / "beats.json"
    candidates_path = input_dir / "candidates.json"

    missing = [p.name for p in (beats_path, candidates_path) if not p.exists()]
    if missing:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(message=f"Missing required input file(s) in {input_dir}: {missing}"),
        )

    beats_data = json.loads(beats_path.read_text(encoding="utf-8"))
    candidates_data = json.loads(candidates_path.read_text(encoding="utf-8"))
    beat_text_by_id = {b["beat_id"]: b["visual_description"] for b in beats_data.get("beats", [])}

    embedder = embedder or _default_embedder(run_id)
    thresholds = thresholds or _default_thresholds()
    cutoff = thresholds["clip_reranking"]["similarity_cutoff"]
    margin = thresholds["clip_reranking"]["close_score_margin"]

    image_failures = 0
    routed_to_05 = 0
    routed_to_06 = 0
    low_retrievable = 0

    for beat_entry in candidates_data.get("candidates_by_beat", []):
        beat_id = beat_entry["beat_id"]
        beat_text = beat_text_by_id.get(beat_id)
        if beat_text is None:
            return StageResponse(
                envelope_id="",
                run_id=run_id,
                stage=STAGE_NAME,
                status=StageStatus.FAILED,
                error=ErrorInfo(message=f"candidates.json references beat_id {beat_id!r} not present in beats.json"),
            )
        text_vec = embedder.embed_text(beat_text)

        best_score = -1.0
        for candidate in beat_entry["candidates"]:
            try:
                img_vec = embedder.embed_image_url(candidate["thumbnail_ref"])
                score = cosine_similarity(text_vec, img_vec)
            except (requests.RequestException, OSError):
                score = -1.0
                image_failures += 1
            candidate["similarity_score"] = round(score, 4)
            best_score = max(best_score, score)

        if best_score >= cutoff + margin:
            route, retrievable = "05_retrieval_verification", "high"
        elif best_score >= cutoff - margin:
            route, retrievable = "05_retrieval_verification", "low"
        else:
            route, retrievable = "06_fallback_generation", "none"

        if route == "05_retrieval_verification":
            routed_to_05 += 1
        else:
            routed_to_06 += 1
        if retrievable == "low":
            low_retrievable += 1

        beat_entry["routing"] = {"route": route, "best_score": round(best_score, 4), "retrievable": retrievable}

    validate_against_schema(candidates_data, "candidates.schema.json")

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "candidates.json").write_text(json.dumps(candidates_data, indent=2), encoding="utf-8")

    summary = (
        f"Scored {len(candidates_data.get('candidates_by_beat', []))} beat(s): "
        f"{routed_to_05} routed to 05_retrieval_verification ({low_retrievable} flagged low/HITL), "
        f"{routed_to_06} routed to 06_fallback_generation."
    )
    if image_failures:
        summary += f" {image_failures} thumbnail fetch/decode failure(s) scored as -1.0 (worst case)."

    return StageResponse(
        envelope_id="",
        run_id=run_id,
        stage=STAGE_NAME,
        status=StageStatus.COMPLETE,
        summary=summary,
        output_manifest=["outputs/candidates.json"],
    )


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python run.py <input_dir> <output_dir> <run_config.yaml>")
        sys.exit(1)
    in_dir, out_dir, config_path = (Path(a) for a in sys.argv[1:4])
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    result = main(in_dir, out_dir, cfg)
    print(json.dumps(result.to_dict(), indent=2))
    sys.exit(0 if result.status == StageStatus.COMPLETE else 1)
