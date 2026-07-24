"""Stage 02_1: screenplay.

Dramatizes one scene's prose into a screenplay - an ordered list of elements
(sluglines, action, dialogue, narration, transitions). This is an AGENT stage:
the adaptation judgment is delegated to a local LLM (see shared/agents/,
config/agents.yaml). This module owns rendering AGENT_PROMPT.md into a system
prompt, calling the model, and parsing/validating/routing its output per
AGENT_PROMPT.md sections 7-8. It never writes screenplay content itself.

The sluglines this stage places are what 02_2_scene_extraction later uses to
find film-scene boundaries; the dialogue/action/narration split is what
02_beat_extraction (shot division) and 09_audio_production (narration) read.
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

STAGE_NAME = "02_1_screenplay"
PROMPT_PATH = Path(__file__).resolve().parents[1] / "AGENT_PROMPT.md"

# Sections 1-6 and 9 are instructions the model acts on; 7/8/10/11/12 are
# Coordinator/human-facing process docs (same split as 02_beat_extraction).
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
        f"tone: {run_config.get('tone', '')}\n\n"
        f"Scene prose to adapt into a screenplay:\n\n{numbered}"
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
    word_count = len(scene_text.split())
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
            "no_screenplay_produced",
            f"The screenplay model produced no usable elements for this scene (invalid JSON: {exc}). "
            "Retry, or does this scene need a different upstream splitting decision?",
            ["Retry generation", "Review scene text manually"],
        )

    # run_id/scene_id are authoritatively known from context - force them rather
    # than trust the model (same reasoning as 02_beat_extraction).
    parsed["run_id"] = run_id
    parsed["scene_id"] = scene_id
    elements = parsed.get("elements") or []

    if not elements:
        return _needs_input(
            run_id,
            "no_screenplay_produced",
            "The screenplay model produced zero elements for this scene. Retry, or reconsider this scene?",
            ["Retry generation", "Review scene text manually"],
        )

    try:
        validate_against_schema(parsed, "screenplay.schema.json")
    except jsonschema.ValidationError as exc:
        return _needs_input(
            run_id,
            "screenplay_invalid_structure",
            f"The screenplay output failed structural validation: {exc.message}. "
            "Retry, or correct manually?",
            ["Retry generation", "Manually correct the screenplay"],
        )

    # The schema can't conditionally require `character` only on dialogue
    # elements, so enforce it in code (AGENT_PROMPT Forbidden Assumption #4 /
    # the screenplay_invalid_structure reason code): a spoken line with no
    # speaker can't be rendered or attributed downstream.
    dialogue_no_speaker = [
        i for i, e in enumerate(elements)
        if e.get("type") == "dialogue" and not (e.get("character") or "").strip()
    ]
    if dialogue_no_speaker:
        return _needs_input(
            run_id,
            "screenplay_invalid_structure",
            f"{len(dialogue_no_speaker)} dialogue element(s) have no speaker (character). "
            "Retry, or correct manually?",
            ["Retry generation", "Manually correct the screenplay"],
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "screenplay.json").write_text(json.dumps(parsed, indent=2), encoding="utf-8")

    n_slug = sum(1 for e in elements if e.get("type") == "slugline")
    n_dialogue = sum(1 for e in elements if e.get("type") == "dialogue")
    n_action = sum(1 for e in elements if e.get("type") == "action")
    n_narration = sum(1 for e in elements if e.get("type") == "narration")

    summary = (
        f"Adapted scene {scene_id} into {len(elements)} screenplay element(s) "
        f"({n_slug} slugline, {n_action} action, {n_dialogue} dialogue, {n_narration} narration)."
    )
    only_slugline = len(elements) == n_slug
    over_summarized = word_count > 150 and len(elements) < 3
    if only_slugline:
        summary += " HITL: only sluglines, no action/dialogue/narration."
    if over_summarized:
        summary += " HITL: scene appears over-summarized."

    return StageResponse(
        envelope_id="",
        run_id=run_id,
        stage=STAGE_NAME,
        status=StageStatus.COMPLETE,
        summary=summary,
        output_manifest=["outputs/screenplay.json"],
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
