"""Stage 07: editorial_direction.

Given approved beats + winning assets, decides shot subdivision, hold
durations, and transitions per beat boundary. Originally an AGENT stage
(CLAUDE.md rule 4) - reclassified to CODE by default 2026-07-18 (see
ARCHITECTURE.md change log / DECISIONS_LOG.md): the agent proved unreliable
across multiple models at shot-to-asset assignment specifically, so the
default path now mechanically turns every retained verified asset into its
own shot (_build_deterministic_edit_plan). AGENT mode remains available by
passing an explicit agent_call (e.g. _default_agent_call for the real Ollama
backend) - vocabulary/range enforcement and HITL trigger detection stay CODE
either way and run unchanged against both paths' output.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Callable

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from shared.agents import AgentBackendError, call_ollama, load_agent_config, resolve_model  # noqa: E402
from shared.envelopes import ErrorInfo, NeedsInputItem, StageResponse, StageStatus, validate_against_schema  # noqa: E402

STAGE_NAME = "07_editorial_direction"
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


def _build_deterministic_edit_plan(
    workable_beat_ids: list[str],
    beats_by_id: dict,
    assets_by_beat_id: dict[str, list[dict]],
    hold_min: float,
    hold_max: float,
    max_shots_per_beat: int,
    min_shot_len: float,
    default_transition: str = "hard-cut",
) -> dict:
    """CODE (not agent) edit plan: every verified asset a beat retained
    becomes its own shot, used as-is (in_s=0, whole clip up to its hold) -
    no LLM judgment over which asset goes to which shot. Human decision
    2026-07-18 (see DECISIONS_LOG.md): the agent was unreliable at this
    specific sub-task across multiple models (hallucinated/cross-contaminated
    asset_id, or just re-using the same asset for every shot despite being
    told to prefer different ones) - mechanically using every retained asset
    sidesteps that reliability ceiling entirely, at the cost of no creative
    transition/rationale judgment (defaults to hard-cut throughout). The
    existing edit_plan_incomplete/over_subdivided/runtime_drift HITL checks
    in main() still run unchanged against this output, so the human-in-the-
    loop safety net is unaffected by skipping the agent."""
    plan_beats = []
    for beat_id in workable_beat_ids:
        beat = beats_by_id[beat_id]
        usable_assets = [a for a in assets_by_beat_id[beat_id] if a["duration_s"] >= min_shot_len][:max_shots_per_beat]
        per_shot_target = beat["est_duration_s"] / len(usable_assets)
        shots = []
        for i, asset in enumerate(usable_assets):
            hold = max(hold_min, min(per_shot_target, hold_max))
            hold = min(hold, asset["duration_s"])
            shot = {"shot_id": f"{beat_id}_s{i + 1}", "in_s": 0.0, "out_s": round(hold, 4), "hold_duration_s": round(hold, 4)}
            if i > 0:
                shot["asset_id"] = asset["asset_id"]
            shots.append(shot)
        plan_beats.append(
            {
                "beat_id": beat_id,
                "asset_id": usable_assets[0]["asset_id"],
                "shots": shots,
                "transition_out": default_transition,
                "rationale": "",
            }
        )
    return {"beats": plan_beats}


def main(
    input_dir: Path,
    output_dir: Path,
    run_config: dict,
    agent_call: AgentCallFn | None = None,
    thresholds: dict | None = None,
    vocab: dict | None = None,
) -> StageResponse:
    run_id = run_config["run_id"]
    beats_path = input_dir / "beats.json"
    assets_path = input_dir / "assets_manifest.json"

    missing = [p.name for p in (beats_path, assets_path) if not p.exists()]
    if missing:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(message=f"Missing required input file(s) in {input_dir}: {missing}"),
        )

    beats_data = json.loads(beats_path.read_text(encoding="utf-8"))
    assets_data = json.loads(assets_path.read_text(encoding="utf-8"))
    beats_by_id = {b["beat_id"]: b for b in beats_data.get("beats", [])}

    assets_by_beat_id: dict[str, list[dict]] = {}
    for a in assets_data.get("assets", []):
        assets_by_beat_id.setdefault(a["beat_id"], []).append(a)
    for beat_assets in assets_by_beat_id.values():
        beat_assets.sort(key=lambda a: a.get("rank", 1))
    # Primary (rank-1, the winning match) asset per beat - used for the
    # too-short check, the beat-level edit_plan.asset_id, and as the default
    # for any shot that doesn't specify its own asset_id.
    asset_by_beat_id = {beat_id: beat_assets[0] for beat_id, beat_assets in assets_by_beat_id.items()}

    thresholds = thresholds or yaml.safe_load((REPO_ROOT / "config" / "thresholds.yaml").read_text(encoding="utf-8"))
    vocab = vocab or yaml.safe_load((REPO_ROOT / "config" / "editorial_vocab.yaml").read_text(encoding="utf-8"))
    min_shot_len = thresholds["editorial"]["min_viable_shot_length_s"]
    max_drift_pct = thresholds["editorial"]["max_runtime_drift_pct"]
    hitl_shot_threshold = vocab["hitl_shot_subdivision_threshold"]
    transition_families = set(vocab["transition_families"])

    pacing = run_config.get("pacing", "standard")
    preset = vocab["pacing_presets"][pacing]
    hold_min, hold_max = preset["hold_duration_s"]["min"], preset["hold_duration_s"]["max"]
    max_shots_per_beat = preset["max_shots_per_beat"]

    ordered_beat_ids = [b["beat_id"] for b in sorted(beats_data.get("beats", []), key=lambda b: b["order"])]
    too_short_beat_ids = [
        beat_id
        for beat_id in ordered_beat_ids
        if beat_id in asset_by_beat_id and asset_by_beat_id[beat_id]["duration_s"] < min_shot_len
    ]
    workable_beat_ids = [
        beat_id for beat_id in ordered_beat_ids if beat_id in asset_by_beat_id and beat_id not in too_short_beat_ids
    ]

    if not workable_beat_ids:
        if too_short_beat_ids:
            return _needs_input(
                run_id,
                "asset_too_short",
                f"Every winning asset is shorter than the {min_shot_len}s minimum viable shot length: {too_short_beat_ids}. "
                "None can proceed without a replacement asset.",
                ["Re-route to fallback generation", "Manually source replacement assets"],
            )
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(message="No beats have a matching winning asset to edit."),
        )

    if agent_call is not None:
        # AGENT mode: explicit opt-in. CLAUDE.md classifies this stage AGENT
        # by default, but a 2026-07-18 human decision (see DECISIONS_LOG.md)
        # made deterministic generation (below) the default after the agent
        # proved unreliable across multiple models specifically at
        # shot-to-asset assignment (hallucinated/cross-contaminated
        # asset_id, or collapsing every shot onto the same asset despite
        # available_assets listing several). Pass agent_call=_default_agent_call
        # explicitly to use the real Ollama backend again.
        beats_payload = [
            {
                "beat_id": beat_id,
                "visual_description": beats_by_id[beat_id]["visual_description"],
                "est_duration_s": beats_by_id[beat_id]["est_duration_s"],
                "asset_id": asset_by_beat_id[beat_id]["asset_id"],
                "asset_duration_s": asset_by_beat_id[beat_id]["duration_s"],
                "available_assets": [
                    {"asset_id": a["asset_id"], "duration_s": a["duration_s"], "rank": a.get("rank", 1)}
                    for a in assets_by_beat_id[beat_id]
                ],
            }
            for beat_id in workable_beat_ids
        ]
        system_prompt = _render_system_prompt(PROMPT_PATH.read_text(encoding="utf-8"))
        user_message = (
            f"pacing: {pacing}\n"
            f"hold_duration_s_range: min={hold_min}, max={hold_max}\n"
            f"max_shots_per_beat: {max_shots_per_beat}\n"
            f"transition_families: {sorted(transition_families)}\n"
            f"min_viable_shot_length_s: {min_shot_len}\n\n"
            f"Beats (in order):\n{json.dumps(beats_payload, indent=2)}"
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
                "edit_plan_incomplete",
                f"The editorial-direction model produced invalid JSON: {exc}. Retry?",
                ["Retry generation", "Review beats manually"],
            )
    else:
        # CODE mode (default, 2026-07-18): every retained verified asset for
        # a beat becomes its own shot, used as-is.
        default_transition = vocab.get("default_beat_transition", "hard-cut")
        if default_transition not in vocab["transition_families"]:
            default_transition = "hard-cut"
        parsed = _build_deterministic_edit_plan(
            workable_beat_ids, beats_by_id, assets_by_beat_id, hold_min, hold_max, max_shots_per_beat, min_shot_len,
            default_transition=default_transition,
        )

    parsed["run_id"] = run_id
    parsed["scene_id"] = beats_data.get("scene_id", "")
    plan_beats = parsed.get("beats") or []
    plan_by_id = {b.get("beat_id"): b for b in plan_beats}

    missing_beats = [bid for bid in workable_beat_ids if bid not in plan_by_id]
    if missing_beats:
        return _needs_input(
            run_id,
            "edit_plan_incomplete",
            f"The edit plan is missing beat(s) {missing_beats}. Retry?",
            ["Retry generation", "Review beats manually"],
        )

    clamp_tolerance_pct = thresholds["editorial"].get("hold_duration_clamp_tolerance_pct", 0)
    clamp_tolerance = (hold_max - hold_min) * clamp_tolerance_pct / 100.0

    validation_errors: list[str] = []
    clamped: list[str] = []
    for beat_id in workable_beat_ids:
        entry = plan_by_id[beat_id]
        shots = entry.get("shots") or []
        if not shots:
            validation_errors.append(f"{beat_id}: no shots")
            continue
        if len(shots) > max_shots_per_beat:
            validation_errors.append(f"{beat_id}: {len(shots)} shots exceeds max_shots_per_beat={max_shots_per_beat}")
        known_asset_ids = {a["asset_id"] for a in assets_by_beat_id[beat_id]}
        for shot in shots:
            shot_asset_id = shot.get("asset_id")
            if shot_asset_id is not None and shot_asset_id not in known_asset_ids:
                validation_errors.append(
                    f"{beat_id}: shot {shot.get('shot_id')!r} references asset_id {shot_asset_id!r} "
                    f"not in this beat's available_assets {sorted(known_asset_ids)}"
                )
            hd = shot.get("hold_duration_s")
            if hd is None:
                validation_errors.append(f"{beat_id}: missing hold_duration_s")
                continue
            if hold_min <= hd <= hold_max:
                pass
            elif (hold_min - clamp_tolerance) <= hd <= (hold_max + clamp_tolerance):
                clamped_value = hold_min if hd < hold_min else hold_max
                clamped.append(f"{beat_id}: {hd}s -> {clamped_value}s")
                shot["hold_duration_s"] = hd = clamped_value
            else:
                validation_errors.append(
                    f"{beat_id}: hold_duration_s={hd} outside [{hold_min}, {hold_max}] "
                    f"even with {clamp_tolerance_pct}% tolerance"
                )
                continue
            # hold_duration_s is the authoritative on-screen duration (see
            # edit_plan.schema.json) - 08_timeline_builder trims to
            # [in_s, in_s + hold_duration_s], which must fit inside [in_s, out_s].
            in_s, out_s = shot.get("in_s"), shot.get("out_s")
            if in_s is None or out_s is None or hd > (out_s - in_s):
                validation_errors.append(
                    f"{beat_id}: hold_duration_s={hd} doesn't fit in the shot's [in_s={in_s}, out_s={out_s}] window"
                )
        transition = entry.get("transition_out", "hard-cut")
        if transition not in transition_families:
            validation_errors.append(f"{beat_id}: transition_out={transition!r} not in transition_families")

    if validation_errors:
        return _needs_input(
            run_id,
            "edit_plan_incomplete",
            f"The edit plan violates configured vocabulary/range constraints: {validation_errors}. Retry?",
            ["Retry generation", "Manually correct the plan"],
        )

    try:
        validate_against_schema(parsed, "edit_plan.schema.json")
    except Exception as exc:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(message="Edit plan failed schema validation", diagnostics=str(exc)),
        )

    computed_total = round(sum(shot["hold_duration_s"] for b in plan_beats for shot in b["shots"]), 4)
    parsed["total_runtime_s"] = computed_total

    hitl_items: list[NeedsInputItem] = []

    def _max_shots_from_one_asset(bid: str) -> int:
        entry = plan_by_id[bid]
        counts: dict[str, int] = {}
        for shot in entry["shots"]:
            resolved = shot.get("asset_id") or entry["asset_id"]
            counts[resolved] = counts.get(resolved, 0) + 1
        return max(counts.values(), default=0)

    over_subdivided = [bid for bid in workable_beat_ids if _max_shots_from_one_asset(bid) > hitl_shot_threshold]
    if over_subdivided:
        hitl_items.append(
            NeedsInputItem(
                reason_code="over_subdivided_shots",
                question=f"Beat(s) {over_subdivided} were subdivided into more than {hitl_shot_threshold} shots from one asset. Review?",
                options=["Approve as-is", "Reduce shot count manually"],
            )
        )

    # The configured default_beat_transition is exempt: this check guards an
    # *agent* leaning on the same dramatic transition as a repeated creative
    # choice - a config-driven mechanical default is not that (2026-07-23,
    # see ARCHITECTURE.md change log).
    exempt_transition = vocab.get("default_beat_transition", "hard-cut")
    dup_transitions = []
    for i in range(len(workable_beat_ids) - 1):
        b1, b2 = workable_beat_ids[i], workable_beat_ids[i + 1]
        t1 = plan_by_id[b1].get("transition_out", "hard-cut")
        t2 = plan_by_id[b2].get("transition_out", "hard-cut")
        if t1 != "hard-cut" and t1 == t2 and t1 != exempt_transition:
            dup_transitions.append((b1, b2, t1))
    if dup_transitions:
        hitl_items.append(
            NeedsInputItem(
                reason_code="repeated_dramatic_transition",
                question=f"Adjacent beat pairs share the same non-default transition: {dup_transitions}. Review?",
                options=["Approve as-is", "Vary the transitions manually"],
            )
        )

    est_total = sum(beats_by_id[bid]["est_duration_s"] for bid in workable_beat_ids)
    drift_pct = abs(computed_total - est_total) / est_total * 100 if est_total else 0.0
    if drift_pct > max_drift_pct:
        hitl_items.append(
            NeedsInputItem(
                reason_code="runtime_drift",
                question=f"Total runtime {computed_total}s drifts {drift_pct:.1f}% from the beat plan's {est_total}s (limit {max_drift_pct}%). Review?",
                options=["Approve as-is", "Revise hold durations manually"],
            )
        )

    if too_short_beat_ids:
        hitl_items.append(
            NeedsInputItem(
                reason_code="asset_too_short",
                question=f"Beat(s) {too_short_beat_ids} have a winning asset shorter than {min_shot_len}s and were excluded from this edit plan. Resolve separately?",
                options=["Re-route to fallback generation", "Manually source replacement assets"],
            )
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "edit_plan.json").write_text(json.dumps(parsed, indent=2), encoding="utf-8")

    summary = f"Edited {len(workable_beat_ids)} beat(s), total_runtime_s={computed_total}."
    if clamped:
        summary += f" {len(clamped)} hold_duration_s value(s) clamped to the nearest bound (within tolerance): {clamped}."
    if hitl_items:
        summary += f" {len(hitl_items)} HITL item(s) need review."

    return StageResponse(
        envelope_id="",
        run_id=run_id,
        stage=STAGE_NAME,
        status=StageStatus.NEEDS_INPUT if hitl_items else StageStatus.COMPLETE,
        summary=summary,
        output_manifest=["outputs/edit_plan.json"],
        needs_input=hitl_items,
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
