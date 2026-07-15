"""Stage 02: beat_extraction.

Interprets one scene's prose into an ordered list of filmable visual beats.
This is the first AGENT stage - the interpretation itself is delegated to a
local LLM (see shared/agents/, config/agents.yaml). This module owns
rendering AGENT_PROMPT.md into a system prompt, calling the model, and
parsing/validating/routing its output per AGENT_PROMPT.md sections 7-8. It
never invents beat content itself - that would defeat the point of keeping
judgment in the agent and mechanics in code (CLAUDE.md rule 4).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Callable

import jsonschema

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

STAGE_NAME = "02_beat_extraction"
PROMPT_PATH = Path(__file__).resolve().parents[1] / "AGENT_PROMPT.md"
MOOD_VOCABULARY = {
    "tense",
    "quiet",
    "ominous",
    "sparse",
    "triumphant",
    "somber",
    "playful",
    "romantic",
    "urgent",
}
# Sections 1-6 and 9 are instructions the model needs to act on. Sections
# 7/8/10/11/12 are Coordinator/human-facing process documentation (NEEDS_INPUT
# reason codes, HITL triggers, failure modes, non-goals, definition of done) -
# useful to a maintainer reading AGENT_PROMPT.md, not to the model at inference
# time, and cutting them keeps the prompt within a small local model's attention.
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


def _find_scene_file(input_dir: Path) -> Path | None:
    candidates = sorted(input_dir.glob("*.txt"))
    return candidates[0] if len(candidates) == 1 else None


def _build_user_message(scene_id: str, scene_text: str, run_config: dict) -> str:
    paragraphs = [p for p in scene_text.strip().split("\n\n") if p.strip()]
    numbered = "\n\n".join(f"[para:{i + 1}] {p}" for i, p in enumerate(paragraphs))
    return (
        f"scene_id: {scene_id}\n"
        f"tone: {run_config.get('tone', '')}\n"
        f"pacing: {run_config.get('pacing', 'standard')}\n"
        f"allowed_mood_tags: {sorted(MOOD_VOCABULARY)}\n\n"
        f"Scene text (paragraphs numbered for text_excerpt_ref):\n\n{numbered}"
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

    scene_file = _find_scene_file(input_dir)
    if scene_file is None:
        found = len(list(input_dir.glob("*.txt")))
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(message=f"Expected exactly one scene .txt file in {input_dir}, found {found}."),
        )

    scene_id = scene_file.stem
    scene_text = scene_file.read_text(encoding="utf-8")
    system_prompt = _render_system_prompt(PROMPT_PATH.read_text(encoding="utf-8"))
    user_message = _build_user_message(scene_id, scene_text, run_config)

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
            "no_scene_beats_produced",
            f"The beat-extraction model produced no usable beats for this scene (invalid JSON: {exc}). "
            "Retry, or does this scene need a different marker/splitting decision upstream?",
            ["Retry generation", "Review scene text manually"],
        )

    # Force-overwrite rather than setdefault(): the model sometimes emits these
    # keys with a null/wrong value (not just omits them), and the run_id/
    # scene_id are always authoritatively known from context anyway (this bit
    # Stage 06 for real - see its run.py comment).
    parsed["run_id"] = run_id
    parsed["scene_id"] = scene_id
    beats = parsed.get("beats") or []

    if not beats:
        return _needs_input(
            run_id,
            "no_scene_beats_produced",
            "The beat-extraction model produced zero beats for this scene. Retry, or reconsider this scene?",
            ["Retry generation", "Review scene text manually"],
        )

    bad_tags = sorted({tag for beat in beats for tag in beat.get("mood_tags", [])} - MOOD_VOCABULARY)
    if bad_tags:
        return _needs_input(
            run_id,
            "mood_tag_outside_vocabulary",
            f"The model used mood tags outside the configured vocabulary: {bad_tags}. "
            "Re-run, or manually correct the tags?",
            ["Retry generation", "Manually correct tags"],
        )

    no_visual_count = sum(1 for b in beats if b.get("no_visual_analog"))
    if no_visual_count > len(beats) / 2:
        return _needs_input(
            run_id,
            "majority_no_visual_analog",
            f"{no_visual_count}/{len(beats)} beats in this scene have no visual analog. "
            "Proceed with a sparse beat list, or reconsider this scene for inclusion in the video?",
            ["Proceed with sparse beat list", "Reconsider scene inclusion"],
        )

    try:
        validate_against_schema(parsed, "beats.schema.json")
    except jsonschema.ValidationError as exc:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(message="Model output failed schema validation", diagnostics=str(exc)),
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "beats.json").write_text(json.dumps(parsed, indent=2), encoding="utf-8")

    long_beats = [b["beat_id"] for b in beats if b.get("est_duration_s", 0) > 15]
    under_segmented = len(beats) < 3 and len(scene_text.split()) > 150

    summary = f"Extracted {len(beats)} beat(s) from scene {scene_id} ({no_visual_count} with no visual analog)."
    if long_beats:
        summary += f" HITL: {len(long_beats)} beat(s) over 15s."
    if under_segmented:
        summary += " HITL: scene appears under-segmented."

    return StageResponse(
        envelope_id="",
        run_id=run_id,
        stage=STAGE_NAME,
        status=StageStatus.COMPLETE,
        summary=summary,
        output_manifest=["outputs/beats.json"],
    )


if __name__ == "__main__":
    import yaml

    if len(sys.argv) != 4:
        print("Usage: python run.py <input_dir> <output_dir> <run_config.yaml>")
        sys.exit(1)
    in_dir, out_dir, config_path = (Path(a) for a in sys.argv[1:4])
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    result = main(in_dir, out_dir, cfg)
    print(json.dumps(result.to_dict(), indent=2))
    sys.exit(0 if result.status == StageStatus.COMPLETE else 1)
