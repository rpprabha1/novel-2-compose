"""Stage 08: timeline_builder.

Materializes the approved edit_plan.json + assets_manifest.json into
timeline.json: absolute timecodes, file references, transition parameters.
Pure transformation - zero creative decisions (those were already made and
approved in Stage 07). CODE per CLAUDE.md.

Shot semantics (see shared/schemas/edit_plan.schema.json): [in_s, out_s] is
the usable source window; hold_duration_s is the authoritative on-screen
duration and is trimmed to [in_s, in_s + hold_duration_s], not to out_s.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from shared.envelopes import ErrorInfo, StageResponse, StageStatus, validate_against_schema  # noqa: E402

STAGE_NAME = "08_timeline_builder"


def main(input_dir: Path, output_dir: Path, run_config: dict) -> StageResponse:
    run_id = run_config["run_id"]
    edit_plan_path = input_dir / "edit_plan.json"
    assets_path = input_dir / "assets_manifest.json"

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

    clips = []
    cursor_s = 0.0
    beats = edit_plan.get("beats", [])
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
                    clip["transition_out"] = {
                        "type": beat.get("transition_out", "hard-cut"),
                        "duration_s": 0.0,
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
