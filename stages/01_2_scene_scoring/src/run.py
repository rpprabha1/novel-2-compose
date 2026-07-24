"""Stage 01_2: scene_scoring.

Scores the downloader stage's clips against the scene, per beat. For each beat
it embeds the beat's visual_description, samples frames from every downloaded
clip, embeds those frames, and averages their CLIP cosine similarity to the
beat text - then ranks the clips best-fit-first for that beat. Deterministic
math (CLAUDE.md classifies CLIP embedding + cosine scoring CODE, like stage 04),
no agent involved.

Output is "ranked scores only": every scored clip appears with its score and
rank; no single winner is forced and nothing is routed. The output is
source-free by design (the downloader lane attaches no source anywhere) - it
carries only a neutral clip_id, a file_ref, and the score/rank.

Frames are extracted once per clip and their embeddings reused across all
beats, so a clip is never re-sampled or re-embedded per beat.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable, Protocol

import numpy as np
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from shared.embeddings import EmbeddingCache, cosine_similarity  # noqa: E402
from shared.envelopes import ErrorInfo, StageResponse, StageStatus, validate_against_schema  # noqa: E402
from shared.media import FFmpegError, extract_frames  # noqa: E402

STAGE_NAME = "01_2_scene_scoring"

FrameExtractorFn = Callable[[Path, Path, int], list[Path]]


class Embedder(Protocol):
    def embed_text(self, text: str) -> np.ndarray: ...
    def embed_image_bytes(self, image_bytes: bytes) -> np.ndarray: ...


def _default_thresholds() -> dict:
    return yaml.safe_load((REPO_ROOT / "config" / "thresholds.yaml").read_text(encoding="utf-8"))


def _default_embedder(run_id: str) -> EmbeddingCache:
    cfg = yaml.safe_load((REPO_ROOT / "config" / "embeddings.yaml").read_text(encoding="utf-8"))
    cache_dir = REPO_ROOT / "shared" / "runs" / run_id / "cache" / "embeddings"
    return EmbeddingCache(model_name=cfg["clip"]["model"], device=cfg["clip"]["device"], cache_dir=cache_dir)


def _resolve_clip_path(file_ref: str, input_dir: Path, clips_base_dir: Path) -> Path | None:
    """Locate a clip file. Tries, in order: the clip staged into this stage's
    inputs/ by basename, then file_ref resolved under clips_base_dir (repo root
    by default, matching how the downloader manifest records repo-relative
    refs), then file_ref as an absolute path. Returns None if none exist."""
    candidates = [
        input_dir / Path(file_ref).name,
        clips_base_dir / file_ref,
        Path(file_ref),
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def _default_vocab() -> dict:
    return yaml.safe_load((REPO_ROOT / "config" / "editorial_vocab.yaml").read_text(encoding="utf-8"))


def _window_length_s(thresholds: dict, vocab: dict, run_config: dict) -> float:
    """The ~4-5s trim window this analyser proposes per clip. Same source as
    07_2's shot length so the analyser's 'which 4-5 seconds' matches the length
    07_2 actually extracts: shot_extraction.target_shot_length_s if set, else the
    active pacing preset's hold_duration_s.max (4.0s for 'standard')."""
    se = thresholds.get("shot_extraction", {})
    override = se.get("target_shot_length_s")
    if override:
        return float(override)
    pacing = run_config.get("pacing", "standard")
    presets = vocab.get("pacing_presets", {})
    preset = presets.get(pacing) or presets.get("standard") or {}
    return float(preset.get("hold_duration_s", {}).get("max", 4.0))


def _best_fit_window(per_frame_scores: list[float], duration: float, window_len: float) -> tuple[float, float]:
    """Center a window of window_len on the highest-scoring sampled frame (the
    most on-topic moment of the clip for this beat), clamped inside [0, duration].
    Frames are evenly spaced at duration*(i+1)/(n+1) (see shared/media.extract_frames)."""
    n = len(per_frame_scores)
    best_i = max(range(n), key=lambda i: per_frame_scores[i])
    center = duration * (best_i + 1) / (n + 1)
    length = min(window_len, duration)
    in_s = min(max(center - length / 2, 0.0), max(duration - length, 0.0))
    out_s = min(in_s + length, duration)
    return round(in_s, 4), round(out_s, 4)


def main(
    input_dir: Path,
    output_dir: Path,
    run_config: dict,
    frame_extractor: FrameExtractorFn | None = None,
    embedder: Embedder | None = None,
    thresholds: dict | None = None,
    clips_base_dir: Path | None = None,
    vocab: dict | None = None,
) -> StageResponse:
    run_id = run_config["run_id"]
    beats_path = input_dir / "beats.json"
    manifest_path = input_dir / "downloader_manifest.json"

    missing = [p.name for p in (beats_path, manifest_path) if not p.exists()]
    if missing:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(message=f"Missing required input file(s) in {input_dir}: {missing}"),
        )

    beats_data = json.loads(beats_path.read_text(encoding="utf-8"))
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    beats = beats_data.get("beats", [])
    clips = manifest_data.get("clips", [])

    frame_extractor = frame_extractor or extract_frames
    embedder = embedder or _default_embedder(run_id)
    thresholds = thresholds or _default_thresholds()
    vocab = vocab or _default_vocab()
    n_frames = thresholds["scene_scoring"]["frames_per_clip"]
    clips_base_dir = clips_base_dir if clips_base_dir is not None else REPO_ROOT
    window_len = _window_length_s(thresholds, vocab, run_config)

    frames_cache_dir = REPO_ROOT / "shared" / "runs" / run_id / "cache" / "scene_scoring"

    # Phase 1: extract + embed frames once per clip; reuse across every beat.
    clip_frame_vecs: dict[str, list[np.ndarray]] = {}
    clip_file_ref: dict[str, str] = {}
    clip_duration: dict[str, float] = {}
    clip_order: list[str] = []
    extraction_failures = 0

    for clip in clips:
        clip_id = clip["clip_id"]
        file_ref = clip.get("file_ref", "")
        path = _resolve_clip_path(file_ref, input_dir, clips_base_dir)
        if path is None:
            extraction_failures += 1
            continue
        try:
            frame_paths = frame_extractor(path, frames_cache_dir / f"{clip_id}_frames", n_frames)
            vecs = [embedder.embed_image_bytes(fp.read_bytes()) for fp in frame_paths]
        except (FFmpegError, OSError):
            extraction_failures += 1
            continue
        if not vecs:
            extraction_failures += 1
            continue
        clip_frame_vecs[clip_id] = vecs
        clip_file_ref[clip_id] = file_ref
        dur = clip.get("duration_s")
        if isinstance(dur, (int, float)) and dur > 0:
            clip_duration[clip_id] = float(dur)
        clip_order.append(clip_id)

    # Phase 2: score every clip against every beat's visual_description, and for
    # each clip propose a best-fit ~window_len trim window centered on that
    # clip's highest-scoring frame for this beat (the analyser's "give the
    # timestamp for trimming" - step 6 of the director flow). Source-free: still
    # only clip_id/file_ref/score/rank plus the neutral window offsets.
    scores_by_beat = []
    empty_beats = 0
    for beat in beats:
        beat_id = beat["beat_id"]
        text_vec = embedder.embed_text(beat["visual_description"])
        ranked = []
        for clip_id in clip_order:
            vecs = clip_frame_vecs[clip_id]
            per_frame = [cosine_similarity(text_vec, v) for v in vecs]
            score = sum(per_frame) / len(per_frame)
            entry = {
                "clip_id": clip_id,
                "file_ref": clip_file_ref[clip_id],
                "score": round(score, 4),
                "frames_scored": len(vecs),
            }
            duration = clip_duration.get(clip_id)
            if duration:
                trim_in, trim_out = _best_fit_window(per_frame, duration, window_len)
                entry["trim_in_s"] = trim_in
                entry["trim_out_s"] = trim_out
            ranked.append(entry)
        # Stable sort by score desc; clip_order breaks ties deterministically.
        ranked.sort(key=lambda r: r["score"], reverse=True)
        for rank, entry in enumerate(ranked, start=1):
            entry["rank"] = rank
        if not ranked:
            empty_beats += 1
        scores_by_beat.append({"beat_id": beat_id, "ranked_clips": ranked})

    output = {
        "run_id": run_id,
        "scene_id": beats_data.get("scene_id", manifest_data.get("scene_id", "")),
        "scores_by_beat": scores_by_beat,
    }
    validate_against_schema(output, "scene_scores.schema.json")

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "scene_scores.json").write_text(json.dumps(output, indent=2), encoding="utf-8")

    scored_clips = len(clip_order)
    summary = (
        f"Scored {scored_clips} clip(s) against {len(beats)} beat(s); "
        f"each beat ranked best-fit-first (ranked scores only, no clip selected)."
    )
    if extraction_failures:
        summary += f" {extraction_failures} clip(s) could not be frame-sampled and were excluded."
    if empty_beats:
        summary += f" {empty_beats} beat(s) have an empty ranking (no clip yielded frames)."

    return StageResponse(
        envelope_id="",
        run_id=run_id,
        stage=STAGE_NAME,
        status=StageStatus.COMPLETE,
        summary=summary,
        output_manifest=["outputs/scene_scores.json"],
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
