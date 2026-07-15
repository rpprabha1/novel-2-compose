"""Stage 05: retrieval_verification.

For each beat routed here by Stage 04, downloads the top-k candidates,
samples frames across the actual clip (not just the thumbnail), and
re-scores. Beats whose top verified candidates are within the close-score
margin are batched for a human tie-break rather than auto-selected
(CLAUDE.md rule 10). CODE + HITL - no agent involved.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

import numpy as np
import requests
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from shared.embeddings import EmbeddingCache, cosine_similarity  # noqa: E402
from shared.envelopes import (  # noqa: E402
    ErrorInfo,
    FallbackRoutedItem,
    NeedsInputItem,
    StageResponse,
    StageStatus,
    validate_against_schema,
)
from shared.manifest import append_manifest_entries  # noqa: E402
from shared.media import FFmpegError, extract_frames  # noqa: E402

STAGE_NAME = "05_retrieval_verification"

DownloaderFn = Callable[[str, Path], None]
FrameExtractorFn = Callable[[Path, Path, int], list[Path]]


class Embedder(Protocol):
    def embed_text(self, text: str) -> np.ndarray: ...
    def embed_image_bytes(self, image_bytes: bytes) -> np.ndarray: ...


def _default_downloader(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.get(url, timeout=60, stream=True)
    resp.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 16):
            f.write(chunk)


def _default_thresholds() -> dict:
    return yaml.safe_load((REPO_ROOT / "config" / "thresholds.yaml").read_text(encoding="utf-8"))


def _default_embedder(run_id: str) -> EmbeddingCache:
    cfg = yaml.safe_load((REPO_ROOT / "config" / "embeddings.yaml").read_text(encoding="utf-8"))
    cache_dir = REPO_ROOT / "shared" / "runs" / run_id / "cache" / "embeddings"
    return EmbeddingCache(model_name=cfg["clip"]["model"], device=cfg["clip"]["device"], cache_dir=cache_dir)


def _asset_entry(run_id: str, beat_id: str, candidate: dict, score: float) -> dict:
    return {
        "beat_id": beat_id,
        "asset_id": candidate["candidate_id"],
        "origin": "retrieved_verified",
        "file_ref": f"shared/runs/{run_id}/cache/videos/{candidate['candidate_id']}.mp4",
        "duration_s": candidate["duration_s"],
        "confidence": round(score, 4),
        "license": candidate["license"],
        "attribution": {
            "source": candidate["source"],
            "creator_required": False,  # true for every source implemented so far (LICENSES.md); revisit if an attribution-requiring source is added
            **({"creator": candidate["creator"]} if candidate.get("creator") else {}),
        },
    }


def main(
    input_dir: Path,
    output_dir: Path,
    run_config: dict,
    downloader: DownloaderFn | None = None,
    frame_extractor: FrameExtractorFn | None = None,
    embedder: Embedder | None = None,
    thresholds: dict | None = None,
    hitl_decisions: dict[str, str] | None = None,
) -> StageResponse:
    run_id = run_config["run_id"]
    candidates_path = input_dir / "candidates.json"
    beats_path = input_dir / "beats.json"

    missing = [p.name for p in (candidates_path, beats_path) if not p.exists()]
    if missing:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(message=f"Missing required input file(s) in {input_dir}: {missing}"),
        )

    candidates_data = json.loads(candidates_path.read_text(encoding="utf-8"))
    beats_data = json.loads(beats_path.read_text(encoding="utf-8"))
    beat_text_by_id = {b["beat_id"]: b["visual_description"] for b in beats_data.get("beats", [])}

    downloader = downloader or _default_downloader
    frame_extractor = frame_extractor or extract_frames
    embedder = embedder or _default_embedder(run_id)
    thresholds = thresholds or _default_thresholds()
    top_k = thresholds["retrieval_verification"]["top_k"]
    n_frames = thresholds["retrieval_verification"]["frames_per_candidate"]
    margin = thresholds["clip_reranking"]["close_score_margin"]
    hitl_decisions = hitl_decisions or {}

    video_cache_dir = REPO_ROOT / "shared" / "runs" / run_id / "cache" / "videos"

    assets: list[dict] = []
    needs_input_items: list[NeedsInputItem] = []
    fallback_items: list[dict] = []
    verification_failures = 0

    for beat_entry in candidates_data.get("candidates_by_beat", []):
        beat_id = beat_entry["beat_id"]
        routing = beat_entry.get("routing")
        if not routing or routing.get("route") != "05_retrieval_verification":
            continue  # not this stage's concern (06's beat, or unrouted)

        beat_text = beat_text_by_id.get(beat_id)
        if beat_text is None:
            return StageResponse(
                envelope_id="",
                run_id=run_id,
                stage=STAGE_NAME,
                status=StageStatus.FAILED,
                error=ErrorInfo(message=f"candidates.json references beat_id {beat_id!r} not present in beats.json"),
            )

        ranked = sorted(beat_entry["candidates"], key=lambda c: c.get("similarity_score", -1), reverse=True)[:top_k]
        text_vec = embedder.embed_text(beat_text)

        verified: list[tuple[dict, float]] = []
        for candidate in ranked:
            if not candidate.get("download_url") or not candidate.get("duration_s"):
                verification_failures += 1
                continue
            dest = video_cache_dir / f"{candidate['candidate_id']}.mp4"
            try:
                if not dest.exists():
                    downloader(candidate["download_url"], dest)
                frames_dir = video_cache_dir / f"{candidate['candidate_id']}_frames"
                frame_paths = frame_extractor(dest, frames_dir, n_frames)
                frame_scores = [
                    cosine_similarity(text_vec, embedder.embed_image_bytes(fp.read_bytes())) for fp in frame_paths
                ]
                verified_score = sum(frame_scores) / len(frame_scores)
            except (requests.RequestException, FFmpegError, OSError):
                verification_failures += 1
                continue
            verified.append((candidate, verified_score))

        if not verified:
            fallback_items.append(
                {
                    "item_id": beat_id,
                    "reason_code": "verification_failed",
                    "detail": f"All {len(ranked)} top-k candidate(s) failed to download or verify.",
                }
            )
            continue

        verified.sort(key=lambda pair: pair[1], reverse=True)

        if beat_id in hitl_decisions:
            chosen_id = hitl_decisions[beat_id]
            match = next(((c, s) for c, s in verified if c["candidate_id"] == chosen_id), None)
            if match is None:
                return StageResponse(
                    envelope_id="",
                    run_id=run_id,
                    stage=STAGE_NAME,
                    status=StageStatus.FAILED,
                    error=ErrorInfo(
                        message=f"hitl_decisions references candidate_id {chosen_id!r} for beat {beat_id!r}, "
                        "which isn't among its verified candidates."
                    ),
                )
            assets.append(_asset_entry(run_id, beat_id, match[0], match[1]))
            continue

        top_candidate, top_score = verified[0]
        second_score = verified[1][1] if len(verified) > 1 else -1.0
        if len(verified) == 1 or (top_score - second_score) >= margin:
            assets.append(_asset_entry(run_id, beat_id, top_candidate, top_score))
        else:
            needs_input_items.append(
                NeedsInputItem(
                    reason_code="close_score_tiebreak",
                    question=(
                        f"Beat {beat_id}: after verification, the top candidates are still within "
                        f"the close-score margin ({margin}). Pick one."
                    ),
                    options=[
                        f"{c['candidate_id']} (verified_score={s:.3f}, {c['url']})" for c, s in verified
                    ],
                )
            )

    output_manifest = []
    if assets:
        output = {"run_id": run_id, "scene_id": candidates_data.get("scene_id", ""), "assets": assets}
        validate_against_schema(output, "assets_manifest.schema.json")
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "assets_manifest.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
        output_manifest.append("outputs/assets_manifest.json")

        manifest_entries = []
        seen_ids: set[str] = set()
        for asset in assets:
            if asset["asset_id"] in seen_ids:
                continue
            seen_ids.add(asset["asset_id"])
            entry = {
                "entry_id": asset["asset_id"],
                "kind": "footage",
                "fetched_by_stage": STAGE_NAME,
                "fetched_at": "",
                "source": asset["attribution"]["source"],
                "license": asset["license"],
                "attribution_required": asset["attribution"]["creator_required"],
            }
            if asset["attribution"].get("creator"):
                entry["creator"] = asset["attribution"]["creator"]
            manifest_entries.append(entry)
        now = datetime.now(timezone.utc).isoformat()
        for entry in manifest_entries:
            entry["fetched_at"] = now
        append_manifest_entries(REPO_ROOT / "shared" / "runs" / run_id, run_id, manifest_entries)

    summary = f"Verified {len(assets)} beat(s), {len(needs_input_items)} need a human tie-break, {len(fallback_items)} routed to fallback."
    if verification_failures:
        summary += f" {verification_failures} candidate verification attempt(s) failed and were skipped."

    if needs_input_items:
        status = StageStatus.NEEDS_INPUT
    elif fallback_items:
        status = StageStatus.FALLBACK_ROUTED
    else:
        status = StageStatus.COMPLETE

    return StageResponse(
        envelope_id="",
        run_id=run_id,
        stage=STAGE_NAME,
        status=status,
        summary=summary,
        output_manifest=output_manifest,
        needs_input=needs_input_items,
        fallback_routed=[FallbackRoutedItem(**item) for item in fallback_items],
    )


if __name__ == "__main__":
    if len(sys.argv) not in (4, 5):
        print("Usage: python run.py <input_dir> <output_dir> <run_config.yaml> [hitl_decisions.json]")
        sys.exit(1)
    in_dir, out_dir, config_path = (Path(a) for a in sys.argv[1:4])
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    decisions = None
    if len(sys.argv) == 5:
        decisions = json.loads(Path(sys.argv[4]).read_text(encoding="utf-8"))
    result = main(in_dir, out_dir, cfg, hitl_decisions=decisions)
    print(json.dumps(result.to_dict(), indent=2))
    sys.exit(0 if result.status == StageStatus.COMPLETE else 1)
