"""Stage 02_2: scene_extraction.

Segments one source passage's screenplay (from 02_1_screenplay) into distinct
film scenes and emits the canonical scenes_manifest.json + one .txt per scene
that the rest of the pipeline consumes. HYBRID (CLAUDE.md §2 pattern, like 09):

  * AGENT half - deciding scene boundaries (where one place/time ends and the
    next begins), writing each scene's heading/summary, and rendering the
    scene's action+narration into readable prose. Delegated to a local LLM
    (shared/agents/, config/agents.yaml).
  * CODE half - validating the segmentation, assigning scene_id / chapter /
    scene numbers / file paths, writing the .txt files, and building a
    schema-valid scenes_manifest.json.

Scene splitting used to be pure CODE in 01_manuscript_ingestion (marker-based).
This stage adds agent judgment so scenes can be found from a screenplay's
sluglines (including sub-scene boundaries the author's markers never marked) -
the CODE->AGENT reclassification is logged in ARCHITECTURE.md's change log.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Callable

import jsonschema
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from shared.agents import (  # noqa: E402
    AgentBackendError,
    call_ollama,
    load_agent_config,
    resolve_model,
)
from shared.envelopes import (  # noqa: E402
    ErrorInfo,
    NeedsInputItem,
    StageResponse,
    StageStatus,
    validate_against_schema,
)

STAGE_NAME = "02_2_scene_extraction"
PROMPT_PATH = Path(__file__).resolve().parents[1] / "AGENT_PROMPT.md"

_INCLUDED_SECTION_NUMBERS = {"1", "2", "3", "4", "5", "6", "9"}

AgentCallFn = Callable[[str, str], str]


def _render_system_prompt(prompt_md: str) -> str:
    sections = re.split(r"(?m)^## (\d+)\. (.+)$", prompt_md)
    parts = []
    for i in range(1, len(sections), 3):
        num, title, body = sections[i], sections[i + 1], sections[i + 2]
        if num in _INCLUDED_SECTION_NUMBERS:
            parts.append(f"## {num}. {title}{body}")
    parts.append("\nOutput ONLY the JSON object described above. No markdown fences, no explanation.")
    return "\n".join(parts)


def _default_agent_call(system_prompt: str, user_message: str) -> str:
    agent_config = load_agent_config(REPO_ROOT)
    model = resolve_model(agent_config, STAGE_NAME)
    ollama_cfg = agent_config["ollama"]
    result = call_ollama(
        system_prompt=system_prompt,
        user_message=user_message,
        model=model,
        host=ollama_cfg["host"],
        timeout_s=ollama_cfg["timeout_s"],
        json_mode=(ollama_cfg.get("format") == "json"),
        options=ollama_cfg.get("options"),
    )
    return result.raw_text


def _render_elements_for_prompt(elements: list[dict]) -> str:
    lines = []
    for i, el in enumerate(elements, start=1):
        etype = el.get("type", "action")
        text = el.get("text", "")
        if etype == "dialogue" and el.get("character"):
            lines.append(f"[{i}] ({etype}) {el['character']}: {text}")
        else:
            lines.append(f"[{i}] ({etype}) {text}")
    return "\n".join(lines)


def _build_user_message(source_scene_id: str, elements: list[dict]) -> str:
    return (
        f"source_scene_id: {source_scene_id}\n\n"
        f"Screenplay elements (in order) to segment into film scenes:\n\n"
        f"{_render_elements_for_prompt(elements)}"
    )


def _strip_wrapper(raw_text: str) -> str:
    text = raw_text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence_match:
        return fence_match.group(1)
    brace_match = re.search(r"(\{.*\})", text, re.DOTALL)
    if brace_match:
        return brace_match.group(1)
    return text


def _parse_source_scene_id(source_scene_id: str, run_config: dict) -> tuple[int, int]:
    """Derive (chapter_number, base_scene_number) for the manifest from the
    source scene_id (e.g. 'ch1_sc1' -> (1, 1)), falling back to run_config
    base_chapter/base_scene or 1. These are metadata; scene_id is the real
    downstream key."""
    m = re.match(r"ch(\d+)_sc(\d+)", source_scene_id or "")
    if m:
        return int(m.group(1)), int(m.group(2))
    return int(run_config.get("base_chapter", 1)), int(run_config.get("base_scene", 1))


def _needs_input(run_id: str, reason_code: str, question: str, options: list[str]) -> StageResponse:
    return StageResponse(
        envelope_id="",
        run_id=run_id,
        stage=STAGE_NAME,
        status=StageStatus.NEEDS_INPUT,
        needs_input=[NeedsInputItem(reason_code=reason_code, question=question, options=options)],
    )


def main(
    input_dir: Path,
    output_dir: Path,
    run_config: dict,
    agent_call: AgentCallFn | None = None,
) -> StageResponse:
    run_id = run_config["run_id"]
    agent_call = agent_call or _default_agent_call

    screenplay_path = input_dir / "screenplay.json"
    if not screenplay_path.exists():
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(message=f"screenplay.json not found in {input_dir}"),
        )

    screenplay = json.loads(screenplay_path.read_text(encoding="utf-8"))
    source_scene_id = screenplay.get("scene_id", "")
    elements = screenplay.get("elements") or []
    if not elements:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(message="screenplay.json has no elements to segment"),
        )

    system_prompt = _render_system_prompt(PROMPT_PATH.read_text(encoding="utf-8"))
    user_message = _build_user_message(source_scene_id, elements)

    try:
        raw_response = agent_call(system_prompt, user_message)
    except AgentBackendError as exc:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(message="Agent backend call failed", diagnostics=str(exc)),
        )

    try:
        parsed = json.loads(_strip_wrapper(raw_response))
    except json.JSONDecodeError as exc:
        return _needs_input(
            run_id,
            "no_scenes_segmented",
            f"The scene-extraction model produced no scenes (invalid JSON: {exc}). Retry, or segment manually?",
            ["Retry generation", "Segment manually"],
        )

    parsed["run_id"] = run_id
    scenes = parsed.get("scenes") or []
    if not scenes:
        return _needs_input(
            run_id,
            "no_scenes_segmented",
            "The scene-extraction model produced zero scenes from this screenplay. Retry, or segment manually?",
            ["Retry generation", "Segment manually"],
        )

    try:
        validate_against_schema(parsed, "scene_segmentation.schema.json")
    except jsonschema.ValidationError as exc:
        return _needs_input(
            run_id,
            "no_scenes_segmented",
            f"The scene-extraction output failed schema validation: {exc.message}. Retry, or segment manually?",
            ["Retry generation", "Segment manually"],
        )

    # Degenerate-render guard: a scene whose text is empty or just its heading
    # carries nothing for shot division to work from.
    for sc in scenes:
        text = (sc.get("text") or "").strip()
        if not text or text == (sc.get("heading") or "").strip():
            return _needs_input(
                run_id,
                "scene_missing_text",
                "One or more segmented scenes have no usable scene text. Retry, or correct manually?",
                ["Retry generation", "Manually correct the scene text"],
            )

    # --- CODE half: assign ids/metadata, write .txt files, build manifest ---
    chapter_number, base_scene = _parse_source_scene_id(source_scene_id, run_config)
    multi = len(scenes) > 1
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest_scenes = []
    for k, sc in enumerate(scenes):
        scene_id = source_scene_id if not multi else f"{source_scene_id}_p{k + 1}"
        file_name = f"{scene_id}.txt"
        (output_dir / file_name).write_text((sc.get("text") or "").strip() + "\n", encoding="utf-8")
        entry = {
            "scene_id": scene_id,
            "order": k,
            "chapter_number": chapter_number,
            "scene_number_in_chapter": base_scene + k,
            "heading_text": sc.get("heading", ""),
            "file_ref": f"outputs/{file_name}",
        }
        pov = sc.get("pov_character") or run_config.get("pov_character")
        if pov:
            entry["pov_character"] = pov
        manifest_scenes.append(entry)

    manifest = {
        "run_id": run_id,
        "manuscript_ref": run_config.get("manuscript_ref") or f"screenplay:{source_scene_id}",
        "scenes": manifest_scenes,
    }
    try:
        validate_against_schema(manifest, "scenes_manifest.schema.json")
    except jsonschema.ValidationError as exc:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(message="Built scenes_manifest failed schema validation", diagnostics=str(exc)),
        )
    (output_dir / "scenes_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    over_split = len(scenes) > 6
    summary = f"Segmented screenplay for {source_scene_id} into {len(scenes)} film scene(s)."
    if over_split:
        summary += " HITL: more than 6 scenes from one passage (possible over-splitting)."

    return StageResponse(
        envelope_id="",
        run_id=run_id,
        stage=STAGE_NAME,
        status=StageStatus.COMPLETE,
        summary=summary,
        output_manifest=["outputs/scenes_manifest.json"] + [s["file_ref"] for s in manifest_scenes],
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
