"""Stage 06: fallback_generation.

Originally HYBRID (agent writes an image-generation prompt from beat data,
code renders it via sd-turbo diffusion + Ken Burns zoompan). Reclassified to
CODE by default 2026-07-18 (see ARCHITECTURE.md change log): sd-turbo
repeatedly exhausted RAM/disk loading on a constrained dev machine. Default
path was originally a plain ffmpeg text card (the beat's own
visual_description rendered as on-screen text); changed 2026-07-23 (see
ARCHITECTURE.md change log) to a Ken-Burns-animated mood-colored gradient
with no text at all, since 09_audio_production's TTS already speaks that
same text aloud - showing it again on screen was redundant and read as a
broken slideshow. Both variants have no model-loading risk. AGENT+diffusion
mode remains fully implemented and available by passing an explicit
agent_call. Only processes beats Stage 04 routed here
(routing.route == "06_fallback_generation").
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from shared.agents import AgentBackendError, call_ollama, load_agent_config, resolve_model  # noqa: E402
from shared.envelopes import ErrorInfo, NeedsInputItem, StageResponse, StageStatus, validate_against_schema  # noqa: E402
from shared.generation import generate_image  # noqa: E402
from shared.manifest import append_manifest_entries  # noqa: E402
from shared.media import FFmpegError, generate_mood_visual, ken_burns_zoompan  # noqa: E402

STAGE_NAME = "06_fallback_generation"
PROMPT_PATH = Path(__file__).resolve().parents[1] / "AGENT_PROMPT.md"
_INCLUDED_SECTION_NUMBERS = {"1", "2", "3", "4", "5", "6", "9"}

AgentCallFn = Callable[[str, str], str]
ImageGeneratorFn = Callable[[str, str, Path], None]
ZoompanFn = Callable[[Path, Path, float], None]
MoodVisualRendererFn = Callable[[list, float, Path], None]


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


def _default_image_generator(prompt: str, negative_prompt: str, dest_path: Path) -> None:
    cfg = yaml.safe_load((REPO_ROOT / "config" / "image_gen.yaml").read_text(encoding="utf-8"))
    generate_image(
        prompt=prompt,
        negative_prompt=negative_prompt,
        dest_path=dest_path,
        model=cfg["model"],
        device=cfg["device"],
        num_inference_steps=cfg["num_inference_steps"],
        guidance_scale=cfg["guidance_scale"],
        image_size=cfg["image_size"],
    )


def _default_zoompan(image_path: Path, output_path: Path, duration_s: float) -> None:
    ken_burns_zoompan(image_path, output_path, duration_s)


def _default_mood_visual_renderer(mood_tags: list[str], duration_s: float, dest_path: Path) -> None:
    cfg = yaml.safe_load((REPO_ROOT / "config" / "fallback_visual.yaml").read_text(encoding="utf-8"))
    color_map = cfg.get("mood_color_map", {})
    color1, color2 = cfg["default_color1"], cfg["default_color2"]
    for tag in mood_tags:
        if tag in color_map:
            color1, color2 = color_map[tag]
            break
    generate_mood_visual(
        dest_path=dest_path,
        duration_s=duration_s,
        width=cfg["width"],
        height=cfg["height"],
        fps=cfg["fps"],
        color1=color1,
        color2=color2,
        zoom_end=cfg.get("zoom_end", 1.12),
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
    image_generator: ImageGeneratorFn | None = None,
    zoompan: ZoompanFn | None = None,
    mood_visual_renderer: MoodVisualRendererFn | None = None,
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
    beats_by_id = {b["beat_id"]: b for b in beats_data.get("beats", [])}

    routed_beat_ids = [
        entry["beat_id"]
        for entry in candidates_data.get("candidates_by_beat", [])
        if (entry.get("routing") or {}).get("route") == "06_fallback_generation"
    ]

    if not routed_beat_ids:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.COMPLETE,
            summary="No beats routed to fallback generation - nothing to do.",
            output_manifest=[],
        )

    render_failures: list[str] = []
    output_manifest = ["outputs/assets_manifest.json"]

    if agent_call is not None:
        # AGENT + diffusion mode: explicit opt-in. CLAUDE.md classified this
        # stage HYBRID by default, but a 2026-07-18 human decision (see
        # DECISIONS_LOG.md) made the lightweight CODE path (below) the
        # default after sd-turbo repeatedly exhausted RAM/disk for real on a
        # constrained machine. Pass agent_call=_default_agent_call to use
        # the real Ollama + sd-turbo backend again.
        image_generator = image_generator or _default_image_generator
        zoompan = zoompan or _default_zoompan

        style_cfg = yaml.safe_load((REPO_ROOT / "config" / "visual_style.yaml").read_text(encoding="utf-8"))
        tone = run_config.get("tone", "")
        style_modifiers = style_cfg["tone_style_modifiers"].get(tone, [])
        negative_defaults = style_cfg["negative_prompt_defaults"]
        max_words = style_cfg["max_prompt_words"]
        unsafe_terms = [t.lower() for t in style_cfg["unsafe_keyword_screen"]]

        beats_payload = []
        for beat_id in routed_beat_ids:
            beat = beats_by_id.get(beat_id)
            if beat is None:
                return StageResponse(
                    envelope_id="",
                    run_id=run_id,
                    stage=STAGE_NAME,
                    status=StageStatus.FAILED,
                    error=ErrorInfo(message=f"candidates.json references beat_id {beat_id!r} not present in beats.json"),
                )
            beats_payload.append(
                {
                    "beat_id": beat_id,
                    "visual_description": beat["visual_description"],
                    "mood_tags": beat.get("mood_tags", []),
                }
            )

        system_prompt = _render_system_prompt(PROMPT_PATH.read_text(encoding="utf-8"))
        user_message = (
            f"tone: {tone}\n"
            f"style_modifiers: {style_modifiers}\n"
            f"negative_prompt_defaults: {negative_defaults}\n"
            f"max_prompt_words: {max_words}\n\n"
            f"Beats:\n{json.dumps(beats_payload, indent=2)}"
        )

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
                "no_prompts_produced",
                f"The fallback-generation model produced no usable prompts (invalid JSON: {exc}). Retry?",
                ["Retry generation", "Review beats manually"],
            )

        # Force-overwrite rather than setdefault(): the model sometimes emits
        # these keys with a null/wrong value (not just omits them), and the
        # run_id/scene_id are always authoritatively known from context anyway.
        parsed["run_id"] = run_id
        parsed["scene_id"] = candidates_data.get("scene_id", "")
        prompts = parsed.get("prompts") or []

        if len(prompts) != len(routed_beat_ids):
            return _needs_input(
                run_id,
                "no_prompts_produced",
                f"Expected {len(routed_beat_ids)} prompt(s), got {len(prompts)}. Retry?",
                ["Retry generation", "Review beats manually"],
            )

        flagged = [
            p["beat_id"] for p in prompts if any(term in p.get("image_prompt", "").lower() for term in unsafe_terms)
        ]
        if flagged:
            return _needs_input(
                run_id,
                "unsafe_content_flagged",
                f"Generated prompt(s) for beat(s) {flagged} matched the unsafe-content keyword screen. Human review required before rendering.",
                ["Approve and render anyway", "Rewrite prompt manually", "Skip these beats"],
            )

        try:
            validate_against_schema(parsed, "fallback_prompt.schema.json")
        except Exception as exc:
            return StageResponse(
                envelope_id="",
                run_id=run_id,
                stage=STAGE_NAME,
                status=StageStatus.FAILED,
                error=ErrorInfo(message="Prompt output failed schema validation", diagnostics=str(exc)),
            )

        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "fallback_prompt.json").write_text(json.dumps(parsed, indent=2), encoding="utf-8")
        output_manifest.append("outputs/fallback_prompt.json")

        stills_dir = REPO_ROOT / "shared" / "runs" / run_id / "cache" / "generated_stills"
        videos_dir = REPO_ROOT / "shared" / "runs" / run_id / "cache" / "generated_videos"

        assets = []
        for p in prompts:
            beat_id = p["beat_id"]
            beat = beats_by_id[beat_id]
            duration_s = beat["est_duration_s"]
            still_path = stills_dir / f"{beat_id}.png"
            video_path = videos_dir / f"{beat_id}.mp4"
            try:
                if not still_path.exists():
                    image_generator(p["image_prompt"], p["negative_prompt"], still_path)
                if not video_path.exists():
                    zoompan(still_path, video_path, duration_s)
            except (FFmpegError, OSError) as exc:
                render_failures.append(f"{beat_id}: {exc}")
                continue

            assets.append(
                {
                    "beat_id": beat_id,
                    "asset_id": f"generated_{beat_id}",
                    "origin": "generated_fallback",
                    "file_ref": f"shared/runs/{run_id}/cache/generated_videos/{beat_id}.mp4",
                    "duration_s": duration_s,
                    "license": "Generated (local stabilityai/sd-turbo) - no license required",
                    "attribution": {"source": "generated", "creator_required": False},
                }
            )
        scene_id = parsed["scene_id"]
    else:
        # CODE mode (default; mood-visual since 2026-07-23, see ARCHITECTURE.md
        # change log): a Ken-Burns-animated mood-colored gradient per beat, no
        # text - no LLM call, no diffusion model, no RAM/disk risk.
        mood_visual_renderer = mood_visual_renderer or _default_mood_visual_renderer
        fallback_visual_cfg = yaml.safe_load((REPO_ROOT / "config" / "fallback_visual.yaml").read_text(encoding="utf-8"))
        min_card_duration = fallback_visual_cfg["min_duration_s"]
        videos_dir = REPO_ROOT / "shared" / "runs" / run_id / "cache" / "generated_videos"

        assets = []
        for beat_id in routed_beat_ids:
            beat = beats_by_id.get(beat_id)
            if beat is None:
                return StageResponse(
                    envelope_id="",
                    run_id=run_id,
                    stage=STAGE_NAME,
                    status=StageStatus.FAILED,
                    error=ErrorInfo(message=f"candidates.json references beat_id {beat_id!r} not present in beats.json"),
                )
            # A generated visual is synthetic and has no natural duration
            # ceiling like real footage does, so it's rendered at least
            # min_duration_s regardless of est_duration_s - the beat's own
            # (rough) visual estimate, not a reliable predictor of real TTS
            # narration length.
            duration_s = max(beat["est_duration_s"], min_card_duration)
            video_path = videos_dir / f"{beat_id}.mp4"
            try:
                if not video_path.exists():
                    mood_visual_renderer(beat.get("mood_tags", []), duration_s, video_path)
            except (FFmpegError, OSError) as exc:
                render_failures.append(f"{beat_id}: {exc}")
                continue

            assets.append(
                {
                    "beat_id": beat_id,
                    "asset_id": f"generated_{beat_id}",
                    "origin": "generated_fallback",
                    "file_ref": f"shared/runs/{run_id}/cache/generated_videos/{beat_id}.mp4",
                    "duration_s": duration_s,
                    "license": "Generated (mood visual) - no license required",
                    "attribution": {"source": "generated", "creator_required": False},
                }
            )
        scene_id = candidates_data.get("scene_id", "")

    if render_failures:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(
                message=f"{len(render_failures)} beat(s) failed to render",
                diagnostics="; ".join(render_failures),
            ),
        )

    output = {"run_id": run_id, "scene_id": scene_id, "assets": assets}
    validate_against_schema(output, "assets_manifest.schema.json")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "assets_manifest.json").write_text(json.dumps(output, indent=2), encoding="utf-8")

    now = datetime.now(timezone.utc).isoformat()
    manifest_entries = [
        {
            "entry_id": a["asset_id"],
            "kind": "generated_image",
            "fetched_by_stage": STAGE_NAME,
            "fetched_at": now,
            "source": "generated",
            "license": a["license"],
            "attribution_required": False,
        }
        for a in assets
    ]
    append_manifest_entries(REPO_ROOT / "shared" / "runs" / run_id, run_id, manifest_entries)

    return StageResponse(
        envelope_id="",
        run_id=run_id,
        stage=STAGE_NAME,
        status=StageStatus.COMPLETE,
        summary=f"Generated {len(assets)} fallback asset(s) for beat(s) {routed_beat_ids}.",
        output_manifest=output_manifest,
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
