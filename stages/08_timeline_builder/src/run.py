"""Stage 08: timeline_builder.

Materializes the approved edit_plan.json + assets_manifest.json into
timeline.json: absolute timecodes, file references, transition parameters.
Pure transformation - zero creative decisions (those were already made and
approved in Stage 07). CODE per CLAUDE.md.

Shot semantics (see shared/schemas/edit_plan.schema.json): [in_s, out_s] is
the usable source window; hold_duration_s is the authoritative on-screen
duration and is trimmed to [in_s, in_s + hold_duration_s], not to out_s.

Narration reconciliation (2026-07-15, see ARCHITECTURE.md and
09_audio_production/README.md): if inputs/audio_mix.json is present, each
beat's hold_duration_s is extended (never shortened) to at least cover its
narration_stems duration, since audio timing is authoritative for a
narrated-prose format. A beat whose asset can't cover its narration even
starting from in_s=0 is routed FALLBACK_ROUTED (needs a longer/regenerated
asset from Stage 06) rather than silently clipped or looped - the same
"no match is a routed outcome" principle Stage 04/05 already follow. This is
a mechanical, threshold-based routing decision, not a creative one - still
CODE per CLAUDE.md's classification.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from shared.envelopes import (  # noqa: E402
    ErrorInfo,
    FallbackRoutedItem,
    StageResponse,
    StageStatus,
    validate_against_schema,
)

STAGE_NAME = "08_timeline_builder"


_MIN_TILE_S = 1.0
_MAX_TILING_PASSES = 3


def _reconcile_with_narration(
    beats: list[dict],
    narration_duration_by_beat: dict[str, float],
    asset_by_id: dict[str, dict],
    segment_length_s: float,
) -> None:
    """Mutates each beat's shots in place so its visuals cover that beat's
    narration duration. Rewritten 2026-07-23 (see ARCHITECTURE.md change
    log): the original implementation *scaled* each shot's hold up to fill
    the narration window, which (a) demanded 30s+ of continuous footage from
    clips that are typically 5-30s long - the dominant real cause of
    asset_too_short_for_narration text-card replacements - and (b) produced
    one near-static held shot per beat for the whole narration, which the
    author flagged as poorly synced and visually flat.

    Now: *tile* the narration window instead, cycling through the beat's
    shots' assets with consecutive non-overlapping source windows of
    ~segment_length_s (the pacing preset's hold_max - a mechanical
    parameter from config, not a judgment call). A long single asset yields
    multiple different windows of itself (real visual progression); multiple
    assets alternate (the 2026-07-17 multi-angle intent, now sustained
    across full narration length). If every asset's fresh footage is
    exhausted before the window is filled, windows are reused round-robin
    for up to _MAX_TILING_PASSES total passes before the beat is marked
    deficient (under "_narration_shortfall_s") for the caller to route.
    Beats whose only asset is a generated static card keep a single
    unbroken window - cutting between identical static frames produces no
    visible change and only inflates the clip count.
    """
    for beat in beats:
        beat_id = beat["beat_id"]
        required = narration_duration_by_beat.get(beat_id)
        if required is None:
            continue
        shots = beat["shots"]
        current_total = sum(s["hold_duration_s"] for s in shots)
        if required <= current_total + 1e-9:
            continue  # visual hold already covers narration

        resolved: list[tuple[str, float, str]] = []  # (asset_id, duration, origin)
        seen_assets: set[str] = set()
        for shot in shots:
            asset_id = shot.get("asset_id") or beat["asset_id"]
            if asset_id in seen_assets:
                continue
            seen_assets.add(asset_id)
            asset = asset_by_id.get(asset_id)
            if asset is None:
                continue
            resolved.append((asset_id, asset["duration_s"], asset.get("origin", "")))

        beat["_narration_required_s"] = required
        if not resolved:
            beat["_narration_shortfall_s"] = required
            continue

        if len(resolved) == 1 and resolved[0][2] == "generated_fallback":
            asset_id, asset_duration, _ = resolved[0]
            if asset_duration + 1e-9 < required:
                beat["_narration_shortfall_s"] = required - asset_duration
                continue
            beat["shots"] = [
                {
                    "shot_id": f"{beat_id}_t01",
                    "in_s": 0.0,
                    "out_s": round(required, 4),
                    "hold_duration_s": round(required, 4),
                    "asset_id": asset_id,
                }
            ]
            beat["asset_id"] = asset_id
            continue

        tiles: list[dict] = []
        remaining = required
        cursors = {asset_id: 0.0 for asset_id, _, _ in resolved}
        cursor_resets = 0
        # Round-robin rotations: one tile per asset per rotation, alternating
        # assets. _MAX_TILING_PASSES bounds *cursor resets* (window reuse
        # when all fresh footage is spent), not total tiles - a single long
        # asset legitimately yields many consecutive fresh windows.
        while remaining > 1e-9:
            progressed = False
            for asset_id, asset_duration, _origin in resolved:
                if remaining <= 1e-9:
                    break
                cursor = cursors[asset_id]
                available = asset_duration - cursor
                if available < _MIN_TILE_S:
                    continue
                tile_len = min(segment_length_s, remaining, available)
                # Absorb a tiny trailing remainder into this tile rather than
                # emitting a sub-second sliver clip after it.
                if 0 < remaining - tile_len < _MIN_TILE_S and (available - tile_len) >= (remaining - tile_len):
                    tile_len = remaining
                tiles.append(
                    {
                        "shot_id": f"{beat_id}_t{len(tiles) + 1:02d}",
                        "in_s": round(cursor, 4),
                        "out_s": round(cursor + tile_len, 4),
                        "hold_duration_s": round(tile_len, 4),
                        "asset_id": asset_id,
                    }
                )
                cursors[asset_id] = cursor + tile_len
                remaining -= tile_len
                progressed = True
            if not progressed:
                cursor_resets += 1
                if cursor_resets >= _MAX_TILING_PASSES:
                    break
                # Every asset's fresh footage is exhausted - reset cursors so
                # the next rotation reuses windows from the start.
                cursors = {asset_id: 0.0 for asset_id, _, _ in resolved}

        if remaining > 1e-6:
            beat["_narration_shortfall_s"] = remaining
            continue
        beat["shots"] = tiles
        beat["asset_id"] = resolved[0][0]


def main(input_dir: Path, output_dir: Path, run_config: dict, vocab: dict | None = None) -> StageResponse:
    run_id = run_config["run_id"]
    edit_plan_path = input_dir / "edit_plan.json"
    assets_path = input_dir / "assets_manifest.json"
    audio_mix_path = input_dir / "audio_mix.json"
    vocab = vocab or yaml.safe_load((REPO_ROOT / "config" / "editorial_vocab.yaml").read_text(encoding="utf-8"))
    transition_durations_s = vocab["transition_durations_s"]

    missing = [p.name for p in (edit_plan_path, assets_path) if not p.exists()]
    if missing:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(message=f"Missing required input file(s) in {input_dir}: {missing}"),
        )

    edit_plan = json.loads(edit_plan_path.read_text(encoding="utf-8"))
    assets_data = json.loads(assets_path.read_text(encoding="utf-8"))
    asset_by_id = {a["asset_id"]: a for a in assets_data.get("assets", [])}
    beats = edit_plan.get("beats", [])

    fallback_items: list[FallbackRoutedItem] = []
    if audio_mix_path.exists():
        audio_mix = json.loads(audio_mix_path.read_text(encoding="utf-8"))
        narration_duration_by_beat = {s["beat_id"]: s["duration_s"] for s in audio_mix.get("narration_stems", [])}
        # A timed transition (crossfade) borrows its duration from the two
        # clips it joins, shortening the rendered video below the audio-track
        # length - which match_duration() would then freeze-pad at the very
        # end (fine for milliseconds of drift, unwatchable for the ~0.75s x
        # n_beats a crossfade default accumulates). Over-provision each
        # non-final beat's visual window by its outgoing transition duration
        # so the post-transition render still matches the audio (2026-07-23,
        # see ARCHITECTURE.md change log).
        transition_padding = {}
        for i, beat in enumerate(beats):
            if i == len(beats) - 1:
                continue
            t = beat.get("transition_out", "hard-cut")
            transition_padding[beat["beat_id"]] = transition_durations_s.get(t, 0.0)
        required_by_beat = {
            beat_id: duration + transition_padding.get(beat_id, 0.0)
            for beat_id, duration in narration_duration_by_beat.items()
        }
        pacing = run_config.get("pacing", "standard")
        preset = vocab["pacing_presets"].get(pacing) or vocab["pacing_presets"]["standard"]
        segment_length_s = float(preset["hold_duration_s"]["max"])
        _reconcile_with_narration(beats, required_by_beat, asset_by_id, segment_length_s)

        for beat in beats:
            required = beat.pop("_narration_required_s", None)
            shortfall = beat.pop("_narration_shortfall_s", None)
            if required is None or shortfall is None:
                continue
            fallback_items.append(
                FallbackRoutedItem(
                    item_id=beat["beat_id"],
                    reason_code="asset_too_short_for_narration",
                    detail=f"Needs {required:.2f}s to cover narration but the beat's assets tile "
                    f"only {required - shortfall:.2f}s even with window reuse ({shortfall:.2f}s short).",
                )
            )

    if fallback_items:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FALLBACK_ROUTED,
            summary=f"{len(fallback_items)} beat(s) need a longer asset to cover their narration duration.",
            fallback_routed=fallback_items,
        )

    clips = []
    cursor_s = 0.0
    for beat_index, beat in enumerate(beats):
        shots = beat["shots"]
        for shot_index, shot in enumerate(shots):
            resolved_asset_id = shot.get("asset_id") or beat["asset_id"]
            asset = asset_by_id.get(resolved_asset_id)
            if asset is None:
                return StageResponse(
                    envelope_id="",
                    run_id=run_id,
                    stage=STAGE_NAME,
                    status=StageStatus.FAILED,
                    error=ErrorInfo(
                        message=f"edit_plan.json beat {beat['beat_id']!r} shot {shot['shot_id']!r} references "
                        f"asset_id {resolved_asset_id!r} not present in assets_manifest.json"
                    ),
                )

            in_s = shot["in_s"]
            hold_s = shot["hold_duration_s"]
            source_out_s = in_s + hold_s
            if source_out_s > shot["out_s"] + 1e-9:
                return StageResponse(
                    envelope_id="",
                    run_id=run_id,
                    stage=STAGE_NAME,
                    status=StageStatus.FAILED,
                    error=ErrorInfo(
                        message=f"Shot {shot['shot_id']!r}: hold_duration_s doesn't fit in "
                        f"[in_s, out_s] - this should have been caught in Stage 07."
                    ),
                )
            if source_out_s > asset["duration_s"] + 1e-9:
                return StageResponse(
                    envelope_id="",
                    run_id=run_id,
                    stage=STAGE_NAME,
                    status=StageStatus.FAILED,
                    error=ErrorInfo(
                        message=f"Shot {shot['shot_id']!r}: requires {source_out_s}s of source but "
                        f"asset {asset['asset_id']!r} is only {asset['duration_s']}s long."
                    ),
                )

            clip = {
                "shot_id": shot["shot_id"],
                "file_ref": asset["file_ref"],
                "source_in_s": in_s,
                "source_out_s": round(source_out_s, 4),
                "timeline_start_s": round(cursor_s, 4),
                "timeline_end_s": round(cursor_s + hold_s, 4),
            }
            cursor_s += hold_s

            is_last_shot_in_beat = shot_index == len(shots) - 1
            is_last_beat = beat_index == len(beats) - 1
            if is_last_shot_in_beat:
                if not is_last_beat:
                    transition_type = beat.get("transition_out", "hard-cut")
                    clip["transition_out"] = {
                        "type": transition_type,
                        "duration_s": transition_durations_s.get(transition_type, 0.0),
                    }
                # last shot of the last beat: no transition_out, nothing follows.
            else:
                # intra-beat shot boundary: edit_plan.json doesn't specify a
                # transition here, so it's a hard-cut by construction.
                clip["transition_out"] = {"type": "hard-cut", "duration_s": 0.0}

            clips.append(clip)

    timeline = {
        "run_id": run_id,
        "scene_id": edit_plan.get("scene_id", ""),
        "clips": clips,
        "total_duration_s": round(cursor_s, 4),
    }
    validate_against_schema(timeline, "timeline.schema.json")

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "timeline.json").write_text(json.dumps(timeline, indent=2), encoding="utf-8")

    return StageResponse(
        envelope_id="",
        run_id=run_id,
        stage=STAGE_NAME,
        status=StageStatus.COMPLETE,
        summary=f"Built timeline with {len(clips)} clip(s), total_duration_s={timeline['total_duration_s']}.",
        output_manifest=["outputs/timeline.json"],
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
