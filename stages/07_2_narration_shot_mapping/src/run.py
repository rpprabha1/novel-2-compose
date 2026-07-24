"""Stage 07_2: narration_shot_mapping.

The downloader lane's clips are often long (a 3-minute nursery-rhyme video for
what a beat needs 4 seconds of). This stage turns each beat's narration into a
sequence of short, physically-extracted shots cut from that beat's best-matching
downloader clips - the "narration to shot mapping" (author request 2026-07-24,
see ARCHITECTURE.md change log).

For each beat it: takes the top-N ranked clips for that beat from
01_2_scene_scoring, tiles the beat's narration duration (from 09_audio_
production's audio_mix.json) into ~shot-length windows walking distinct
positions across those clips (so a long clip yields real visual progression and
multiple clips alternate, never a frozen hold), and physically extracts each
window as its own short .mp4 via shared/media.trim_clip.

Outputs three files: shot_map.json (the explicit narration->shot mapping),
assets_manifest.json (each extracted shot as a source-free asset), and
edit_plan.json (each beat's shots in order, ready for 08_timeline_builder). All
source-free, consistent with the downloader lane - no platform/url/creator/
license is attached to any clip. Deterministic ffmpeg + arithmetic, no agent
(CLAUDE.md classifies media ops + tiling math CODE, like Stage 08).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Callable

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from shared.downloader_assets import DOWNLOADER_LICENSE, DOWNLOADER_SOURCE  # noqa: E402
from shared.envelopes import ErrorInfo, FallbackRoutedItem, StageResponse, StageStatus, validate_against_schema  # noqa: E402
from shared.media import FFmpegError, probe_duration_s, trim_clip  # noqa: E402

STAGE_NAME = "07_2_narration_shot_mapping"

TrimFn = Callable[[Path, Path, float, float], None]
ProbeFn = Callable[[Path], float]


def _extract_beat_shots(
    beat_id: str,
    candidates: list[tuple[str, str, float, float]],
    total_needed: float,
    shot_len: float,
    min_shot: float,
    max_shots: int,
    run_id: str,
    shots_dir: Path,
    trim: "TrimFn",
    prober: "ProbeFn",
    failures: list[str],
    global_cursor: dict[str, float],
    global_used: dict[str, int],
    state: dict,
) -> list[dict]:
    """Extract short shots covering *total_needed* seconds from this beat's
    *candidates* (clip_id, file_ref, duration, trim_in), in rank order.

    Selection is diversity-first ACROSS THE WHOLE SCENE, driven by three pieces
    of global state threaded across every beat (this is what stops the same few
    clips from carpeting the whole video on a repetitive scene):
      * `global_used` - each shot picks the beat's LEAST-used candidate (ties
        broken by CLIP rank), so footage spreads across the pool instead of
        concentrating on the handful of globally top-scoring clips.
      * `state['last_clip']` - the immediately previous shot's clip is skipped
        when any alternative exists, so no two shots in a row share a clip.
      * `global_cursor` - a per-clip window position that PERSISTS across beats
        (first use starts at 01_2's best-fit `trim_in`, then advances; wraps to 0
        when a clip is exhausted), so a reused clip shows a fresh segment rather
        than the same best-fit frame every time.

    Extraction happens inline: a clip whose trim fails (undecodable/corrupt
    input) is DROPPED for this beat and coverage continues from the rest. Cache
    filenames are content-derived (clip + window), so re-running with a changed
    selection never silently reuses a stale extracted clip. Returns the placed
    shots with their true (ffprobed) durations."""
    rank_of = {cid: i for i, (cid, _, _, _) in enumerate(candidates)}
    by_id = {cid: (ref, dur, trim_in) for cid, ref, dur, trim_in in candidates}
    bad: set[str] = set()
    placed: list[dict] = []
    remaining = total_needed
    while remaining > 1e-9 and len(placed) < max_shots:
        pool = [cid for cid, _, _, _ in candidates if cid not in bad]
        if not pool:
            break
        prev = state.get("last_clip")
        choices = [cid for cid in pool if cid != prev] or pool
        # Least-used-first (spread footage), tie-break by best CLIP rank.
        cid = min(choices, key=lambda c: (global_used.get(c, 0), rank_of[c]))
        ref, dur, trim_in = by_id[cid]

        cursor = global_cursor.get(cid, trim_in)
        if dur - cursor < min_shot:  # clip exhausted - wrap to draw a fresh pass
            cursor = 0.0
        available = dur - cursor
        length = min(shot_len, remaining, available)
        # Absorb a tiny trailing remainder rather than emit a sliver clip.
        if 0 < remaining - length < min_shot and (available - length) >= (remaining - length):
            length = min(remaining, available)

        shot_id = f"{beat_id}_s{len(placed) + 1:02d}"
        # Content-derived cache name: window change -> new file -> fresh extract.
        key = hashlib.sha1(f"{cid}|{round(cursor, 3)}|{round(length, 3)}".encode()).hexdigest()[:10]
        fname = f"{shot_id}__{key}.mp4"
        dest = shots_dir / fname
        try:
            if not dest.exists():
                trim(REPO_ROOT / ref, dest, cursor, length)
            actual = float(prober(dest))
            if actual <= 0:
                raise FFmpegError(f"{shot_id}: extracted clip has non-positive duration")
        except (FFmpegError, OSError) as exc:
            failures.append(f"{shot_id} ({cid}): {exc}")
            bad.add(cid)  # drop this whole clip for the beat, keep covering
            continue

        placed.append({
            "shot_id": shot_id, "clip_id": cid, "file_ref": ref,
            "in_s": round(cursor, 4), "length": round(length, 4),
            "extracted_file_ref": f"shared/runs/{run_id}/cache/shots/{fname}",
            "duration_s": round(actual, 4),
        })
        global_cursor[cid] = cursor + length
        global_used[cid] = global_used.get(cid, 0) + 1
        state["last_clip"] = cid
        remaining -= actual
    return placed


def main(
    input_dir: Path,
    output_dir: Path,
    run_config: dict,
    trim: TrimFn | None = None,
    prober: ProbeFn | None = None,
    thresholds: dict | None = None,
    vocab: dict | None = None,
) -> StageResponse:
    run_id = run_config["run_id"]
    beats_path = input_dir / "beats.json"
    scene_scores_path = input_dir / "scene_scores.json"
    manifest_path = input_dir / "downloader_manifest.json"
    audio_mix_path = input_dir / "audio_mix.json"  # optional (narration durations)

    missing = [p.name for p in (beats_path, scene_scores_path, manifest_path) if not p.exists()]
    if missing:
        return StageResponse(
            envelope_id="", run_id=run_id, stage=STAGE_NAME, status=StageStatus.FAILED,
            error=ErrorInfo(message=f"Missing required input file(s) in {input_dir}: {missing}"),
        )

    beats_data = json.loads(beats_path.read_text(encoding="utf-8"))
    scene_scores = json.loads(scene_scores_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    scene_id = beats_data.get("scene_id", scene_scores.get("scene_id", ""))

    trim = trim or trim_clip
    prober = prober or probe_duration_s
    thresholds = thresholds or yaml.safe_load((REPO_ROOT / "config" / "thresholds.yaml").read_text(encoding="utf-8"))
    vocab = vocab or yaml.safe_load((REPO_ROOT / "config" / "editorial_vocab.yaml").read_text(encoding="utf-8"))

    se = thresholds["shot_extraction"]
    min_shot = se["min_shot_length_s"]
    max_shots = min(se["max_shots_per_beat"], 12)  # 12 = edit_plan.schema.json ceiling
    assets_per_beat = thresholds["downloader_selection"]["assets_per_beat"]
    # Wider candidate pool per beat for cross-beat variety (see config comment);
    # falls back to assets_per_beat so small test fixtures behave as before.
    candidate_pool = se.get("candidate_pool_per_beat") or assets_per_beat
    pacing = run_config.get("pacing", "standard")
    preset = vocab["pacing_presets"].get(pacing) or vocab["pacing_presets"]["standard"]
    target_shot_len = se.get("target_shot_length_s") or float(preset["hold_duration_s"]["max"])
    # Applied only at the LAST shot of each beat (08_timeline_builder hard-cuts
    # every intra-beat shot boundary regardless) - matches 07_editorial_
    # direction's deterministic path, so beat-to-beat cuts still read as the
    # gentle crossfade the author asked for (2026-07-23, see
    # ARCHITECTURE.md), not a uniform hard-cut.
    default_beat_transition = vocab.get("default_beat_transition", "hard-cut")

    clip_dur = {c["clip_id"]: c.get("duration_s") for c in manifest.get("clips", [])}
    clip_ref = {c["clip_id"]: c.get("file_ref") for c in manifest.get("clips", [])}
    ranked_by_beat = {e["beat_id"]: e.get("ranked_clips", []) for e in scene_scores.get("scores_by_beat", [])}

    narration_by_beat: dict[str, float] = {}
    if audio_mix_path.exists():
        audio_mix = json.loads(audio_mix_path.read_text(encoding="utf-8"))
        narration_by_beat = {s["beat_id"]: s["duration_s"] for s in audio_mix.get("narration_stems", [])}

    shots_dir = REPO_ROOT / "shared" / "runs" / run_id / "cache" / "shots"

    shot_map_beats: list[dict] = []
    assets: list[dict] = []
    edit_plan_beats: list[dict] = []
    fallback_items: list[FallbackRoutedItem] = []
    extraction_failures: list[str] = []

    # Scene-wide selection state threaded through every beat so footage variety
    # is a whole-scene property, not a per-beat one (see _extract_beat_shots).
    global_cursor: dict[str, float] = {}
    global_used: dict[str, int] = {}
    select_state: dict = {"last_clip": None}

    for beat in beats_data.get("beats", []):
        beat_id = beat["beat_id"]
        # This beat's candidate clips: its top-`candidate_pool` ranked clips with
        # a real duration + locatable file, in rank order (best-fit first).
        candidates: list[tuple[str, str, float, float]] = []
        for rc in ranked_by_beat.get(beat_id, [])[:candidate_pool]:
            cid = rc["clip_id"]
            dur = clip_dur.get(cid)
            ref = clip_ref.get(cid) or rc.get("file_ref")
            if dur and dur > 0 and ref:
                # First use of a clip starts at 01_2's best-fit trim window
                # (trim_in_s); clamp so at least min_shot of footage remains.
                trim_in = float(rc.get("trim_in_s") or 0.0)
                trim_in = min(max(trim_in, 0.0), max(float(dur) - min_shot, 0.0))
                candidates.append((cid, ref, float(dur), trim_in))

        narration = narration_by_beat.get(beat_id) or beat.get("est_duration_s") or target_shot_len

        if not candidates:
            fallback_items.append(
                FallbackRoutedItem(
                    item_id=beat_id, reason_code="no_scored_clip",
                    detail="No downloader clip could be scored/located for this beat - it contributes no shots.",
                )
            )
            continue

        # If the target shot length would need more than max_shots windows,
        # stretch each shot so the beat still fits the shot ceiling.
        shot_len = target_shot_len
        if narration / shot_len > max_shots:
            shot_len = narration / max_shots

        placed = _extract_beat_shots(
            beat_id, candidates, narration, shot_len, min_shot, max_shots,
            run_id, shots_dir, trim, prober, extraction_failures,
            global_cursor, global_used, select_state,
        )

        beat_shots: list[dict] = []
        beat_edit_shots: list[dict] = []
        narr_cursor = 0.0
        for i, p in enumerate(placed, start=1):
            actual = p["duration_s"]
            asset_id = f"{beat_id}__{p['clip_id']}__s{i:02d}"
            narr_start, narr_end = round(narr_cursor, 4), round(narr_cursor + actual, 4)
            narr_cursor += actual

            beat_shots.append({
                "shot_id": p["shot_id"], "asset_id": asset_id, "source_clip_id": p["clip_id"],
                "source_file_ref": p["file_ref"], "source_in_s": p["in_s"],
                "source_out_s": round(p["in_s"] + p["length"], 4),
                "extracted_file_ref": p["extracted_file_ref"], "duration_s": actual,
                "narration_start_s": narr_start, "narration_end_s": narr_end,
            })
            assets.append({
                "beat_id": beat_id, "asset_id": asset_id, "origin": DOWNLOADER_SOURCE,
                "file_ref": p["extracted_file_ref"], "duration_s": actual, "rank": i,
                "license": DOWNLOADER_LICENSE,
                "attribution": {"source": DOWNLOADER_SOURCE, "creator_required": False},
            })
            beat_edit_shots.append({
                "shot_id": p["shot_id"], "asset_id": asset_id, "in_s": 0.0,
                "out_s": actual, "hold_duration_s": actual,
            })

        if not beat_edit_shots:
            fallback_items.append(
                FallbackRoutedItem(
                    item_id=beat_id, reason_code="shot_extraction_failed",
                    detail=f"All {len(windows)} planned shot(s) failed to extract for this beat.",
                )
            )
            continue

        shot_map_beats.append({
            "beat_id": beat_id, "narration_duration_s": round(narration, 4), "shots": beat_shots,
        })
        edit_plan_beats.append({
            "beat_id": beat_id, "asset_id": beat_edit_shots[0]["asset_id"],
            "shots": beat_edit_shots, "transition_out": default_beat_transition,
        })

    if not edit_plan_beats:
        return StageResponse(
            envelope_id="", run_id=run_id, stage=STAGE_NAME, status=StageStatus.FAILED,
            error=ErrorInfo(
                message="No beat produced any usable shot from the downloader lane.",
                diagnostics="; ".join(extraction_failures) or "no scored clips for any beat",
            ),
        )

    shot_map = {"run_id": run_id, "scene_id": scene_id, "beats": shot_map_beats}
    assets_manifest = {"run_id": run_id, "scene_id": scene_id, "assets": assets}
    total_runtime = round(sum(s["hold_duration_s"] for b in edit_plan_beats for s in b["shots"]), 4)
    edit_plan = {"run_id": run_id, "scene_id": scene_id, "total_runtime_s": total_runtime, "beats": edit_plan_beats}

    validate_against_schema(shot_map, "shot_map.schema.json")
    validate_against_schema(assets_manifest, "assets_manifest.schema.json")
    validate_against_schema(edit_plan, "edit_plan.schema.json")

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "shot_map.json").write_text(json.dumps(shot_map, indent=2), encoding="utf-8")
    (output_dir / "assets_manifest.json").write_text(json.dumps(assets_manifest, indent=2), encoding="utf-8")
    (output_dir / "edit_plan.json").write_text(json.dumps(edit_plan, indent=2), encoding="utf-8")

    n_shots = len(assets)
    summary = (
        f"Mapped narration to {n_shots} extracted shot(s) across {len(edit_plan_beats)} beat(s) "
        f"(~{target_shot_len:.1f}s target, total_runtime_s={total_runtime})."
    )
    if extraction_failures:
        summary += f" {len(extraction_failures)} shot(s) failed to extract and were skipped."
    if fallback_items:
        summary += f" {len(fallback_items)} beat(s) got no shots."

    output_manifest = ["outputs/shot_map.json", "outputs/assets_manifest.json", "outputs/edit_plan.json"]
    status = StageStatus.FALLBACK_ROUTED if fallback_items else StageStatus.COMPLETE
    return StageResponse(
        envelope_id="", run_id=run_id, stage=STAGE_NAME, status=status,
        summary=summary, output_manifest=output_manifest, fallback_routed=fallback_items,
    )


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python run.py <input_dir> <output_dir> <run_config.yaml>")
        sys.exit(1)
    in_dir, out_dir, config_path = (Path(a) for a in sys.argv[1:4])
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    result = main(in_dir, out_dir, cfg)
    print(json.dumps(result.to_dict(), indent=2))
    sys.exit(0 if result.status in (StageStatus.COMPLETE, StageStatus.FALLBACK_ROUTED) else 1)
