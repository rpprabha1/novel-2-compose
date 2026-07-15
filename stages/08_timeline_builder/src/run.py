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


def _reconcile_with_narration(beats: list[dict], narration_duration_by_beat: dict[str, float]) -> None:
    """Mutates each beat's shots in place, extending hold_duration_s (and
    out_s) to cover narration_duration_by_beat where that beat is present
    and longer than the beat's current total hold. Multi-shot beats are
    scaled proportionally so their sum matches the new total. Stashes the
    required duration on each reconciled beat (under "_narration_required_s")
    so the caller can check it against the beat's actual asset length -
    this function has no access to assets_manifest, so it can't itself
    determine whether a beat's asset is too short to cover it."""
    for beat in beats:
        beat_id = beat["beat_id"]
        required = narration_duration_by_beat.get(beat_id)
        if required is None:
            continue
        shots = beat["shots"]
        current_total = sum(s["hold_duration_s"] for s in shots)
        if required <= current_total + 1e-9:
            continue  # visual hold already covers narration

        scale = required / current_total if current_total > 0 else 1.0
        for shot in shots:
            new_hold = shot["hold_duration_s"] * scale if current_total > 0 else required / len(shots)
            shot["hold_duration_s"] = new_hold
            shot["out_s"] = shot["in_s"] + new_hold

        beat["_narration_required_s"] = required


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
        _reconcile_with_narration(beats, narration_duration_by_beat)

        for beat in beats:
            required = beat.pop("_narration_required_s", None)
            if required is None:
                continue
            asset = asset_by_id.get(beat["asset_id"])
            available = asset["duration_s"] if asset else 0.0
            if required > available + 1e-9:
                fallback_items.append(
                    FallbackRoutedItem(
                        item_id=beat["beat_id"],
                        reason_code="asset_too_short_for_narration",
                        detail=f"Needs {required:.2f}s to cover narration but asset {beat['asset_id']!r} is only {available:.2f}s long.",
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
        asset = asset_by_id.get(beat["asset_id"])
        if asset is None:
            return StageResponse(
                envelope_id="",
                run_id=run_id,
                stage=STAGE_NAME,
                status=StageStatus.FAILED,
                error=ErrorInfo(
                    message=f"edit_plan.json beat {beat['beat_id']!r} references asset_id "
                    f"{beat['asset_id']!r} not present in assets_manifest.json"
                ),
            )

        shots = beat["shots"]
        for shot_index, shot in enumerate(shots):
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
