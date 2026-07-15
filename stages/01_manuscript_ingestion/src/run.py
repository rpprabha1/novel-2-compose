"""Stage 01: manuscript_ingestion.

Splits a raw manuscript into per-scene text files using the explicit marker
convention declared in run_config.yaml. Pure text splitting - no interpretation
of content (CLAUDE.md rule 4 / ARCHITECTURE.md stage table classifies this CODE).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from shared.envelopes import (  # noqa: E402
    ErrorInfo,
    NeedsInputItem,
    StageResponse,
    StageStatus,
    TaskEnvelope,
    validate_against_schema,
)

STAGE_NAME = "01_manuscript_ingestion"


def _extract_chapter_number(heading: str, fallback: int) -> int:
    m = re.search(r"(\d+)", heading)
    return int(m.group(1)) if m else fallback


def split_manuscript(text: str, chapter_marker: str, scene_marker: str) -> list[dict]:
    """Pure function: raw manuscript text -> ordered list of scene dicts.

    Each dict: chapter_number, scene_number_in_chapter, heading_text, body.
    Text before the first scene marker (e.g. a chapter marker with no scene
    marker under it yet) is discarded - there is no beat content to carry
    without a scene boundary.
    """
    lines = text.splitlines()
    scenes: list[dict] = []
    current_chapter_number = 0
    current_chapter_heading = ""
    current_scene_heading = ""
    current_scene_number = 0
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        if current_scene_heading and buffer:
            body = "\n".join(buffer).strip("\n")
            heading_text = (
                f"{current_chapter_heading}\n{current_scene_heading}"
                if current_chapter_heading
                else current_scene_heading
            )
            scenes.append(
                {
                    "chapter_number": current_chapter_number,
                    "scene_number_in_chapter": current_scene_number,
                    "heading_text": heading_text,
                    "body": body,
                }
            )
        buffer = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith(chapter_marker):
            flush()
            current_chapter_number = _extract_chapter_number(stripped, current_chapter_number + 1)
            current_chapter_heading = stripped
            current_scene_number = 0
            current_scene_heading = ""
            continue
        if stripped.startswith(scene_marker):
            flush()
            current_scene_number += 1
            current_scene_heading = stripped
            continue
        buffer.append(line)
    flush()
    return scenes


def main(input_dir: Path, output_dir: Path, run_config: dict) -> StageResponse:
    run_id = run_config["run_id"]
    manuscript_path = input_dir / "manuscript.txt"

    if not manuscript_path.exists():
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(message=f"manuscript not found at {manuscript_path}"),
        )

    marker_convention = run_config["scene_marker_convention"]
    chapter_marker = marker_convention["chapter_marker"]
    scene_marker = marker_convention["scene_marker"]
    pov_character = run_config.get("pov_character")

    raw_text = manuscript_path.read_text(encoding="utf-8-sig")
    raw_scenes = split_manuscript(raw_text, chapter_marker, scene_marker)

    if not raw_scenes:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.NEEDS_INPUT,
            needs_input=[
                NeedsInputItem(
                    reason_code="no_scenes_found",
                    question=(
                        f"No scenes found using chapter_marker={chapter_marker!r} / "
                        f"scene_marker={scene_marker!r}. Is the marker convention correct "
                        "for this manuscript?"
                    ),
                    options=[
                        "Adjust scene_marker_convention in run_config.yaml",
                        "Manuscript has no scene markers - treat whole text as one scene",
                    ],
                )
            ],
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    scenes_manifest: dict = {
        "run_id": run_id,
        "manuscript_ref": manuscript_path.as_posix(),
        "scenes": [],
    }
    for order, scene in enumerate(raw_scenes):
        scene_id = f"ch{scene['chapter_number']}_sc{scene['scene_number_in_chapter']}"
        file_name = f"{scene_id}.txt"
        (output_dir / file_name).write_text(scene["body"] + "\n", encoding="utf-8")
        entry = {
            "scene_id": scene_id,
            "order": order,
            "chapter_number": scene["chapter_number"],
            "scene_number_in_chapter": scene["scene_number_in_chapter"],
            "heading_text": scene["heading_text"],
            "file_ref": f"outputs/{file_name}",
        }
        if pov_character:
            entry["pov_character"] = pov_character
        scenes_manifest["scenes"].append(entry)

    validate_against_schema(scenes_manifest, "scenes_manifest.schema.json")
    (output_dir / "scenes_manifest.json").write_text(
        json.dumps(scenes_manifest, indent=2), encoding="utf-8"
    )

    return StageResponse(
        envelope_id="",
        run_id=run_id,
        stage=STAGE_NAME,
        status=StageStatus.COMPLETE,
        summary=(
            f"Split manuscript into {len(raw_scenes)} scene(s) across "
            f"{scenes_manifest['scenes'][-1]['chapter_number']} chapter(s)."
        ),
        output_manifest=[s["file_ref"] for s in scenes_manifest["scenes"]]
        + ["outputs/scenes_manifest.json"],
    )


def run_from_envelope(envelope: TaskEnvelope) -> StageResponse:
    """Bridges the Coordinator's Task Envelope protocol to main(). Speculative
    glue for when 00_coordinator exists - not yet exercised by a real Coordinator."""
    run_config = yaml.safe_load((REPO_ROOT / envelope.run_config_ref).read_text(encoding="utf-8"))
    stage_dir = REPO_ROOT / "stages" / envelope.stage
    response = main(stage_dir / "inputs", stage_dir / "outputs", run_config)
    response.envelope_id = envelope.envelope_id
    return response


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python run.py <input_dir> <output_dir> <run_config.yaml>")
        sys.exit(1)
    in_dir, out_dir, config_path = (Path(a) for a in sys.argv[1:4])
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    result = main(in_dir, out_dir, cfg)
    print(json.dumps(result.to_dict(), indent=2))
    sys.exit(0 if result.status == StageStatus.COMPLETE else 1)
