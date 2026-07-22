#!/usr/bin/env python3
"""Multi-chapter pipeline orchestrator for the Animal Farm full-novel run.

Ad-hoc bulk-run driver, not part of any stage's src/ - it plays the
Coordinator's role (sequencing, staging inputs/outputs between stages,
applying HITL resolutions) for a run spanning all of a novel's chapters in
one unattended pass. Per the human's explicit authorization for this
specific bulk run (2026-07-21/22, see DECISIONS_LOG.md): stages 05
(close-score tie-break) and 09 (music track selection) apply a documented
default policy instead of blocking on a live human prompt, and every
resulting choice is logged for a consolidated after-the-fact summary rather
than approved stage-by-stage. Stages 06/07 use their CODE-default path
(agent_call=None), not the AGENT opt-in.

Correct stage order is 02,03,04,05,06,07,09,08,10,11,12,13 - 08 must run
AFTER 09 because 09's real narration length reconciles 07's visual-only
edit_plan.json into 08's final timeline.json (see 09's own run.py docstring
and DECISIONS_LOG.md's 2026-07-18 entries).
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType

import yaml

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

from shared.envelopes import StageStatus  # noqa: E402
from shared.media import generate_text_card  # noqa: E402
from shared.sources import GeneratedMusicSource, generated_audio_downloader  # noqa: E402

# NB: the run_config directory name ("animal_farm_ch1") and the run_id value
# stored inside it ("animal_farm_ch1_2026_07_21", used by every stage to
# build its shared/runs/<run_id>/... cache paths) are NOT the same string -
# this was a real bug caught before the first real invocation of this script.
RUN_CONFIG_PATH = REPO_ROOT / "shared/runs/animal_farm_ch1/run_config.yaml"
SCENES_MANIFEST_PATH = REPO_ROOT / "stages/01_manuscript_ingestion/outputs/scenes_manifest.json"
_run_id_for_log = yaml.safe_load(RUN_CONFIG_PATH.read_text(encoding="utf-8"))["run_id"]
LOG_PATH = REPO_ROOT / f"shared/runs/{_run_id_for_log}/full_novel_progress.jsonl"

STAGE_NAMES = {
    2: "beat_extraction",
    3: "candidate_fetch",
    4: "clip_reranking",
    5: "retrieval_verification",
    6: "fallback_generation",
    7: "editorial_direction",
    8: "timeline_builder",
    9: "audio_production",
    10: "human_review_gate",
    11: "assembly_render",
    12: "qa_attribution",
    13: "pixel_art_conversion",
}


def stage_dir(n: int) -> Path:
    return REPO_ROOT / f"stages/{n:02d}_{STAGE_NAMES[n]}"


_loaded_modules: dict[int, ModuleType] = {}


def stage_main(n: int):
    if n not in _loaded_modules:
        path = stage_dir(n) / "src" / "run.py"
        spec = importlib.util.spec_from_file_location(f"_orch_stage_{n:02d}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _loaded_modules[n] = mod
    return _loaded_modules[n].main


def clean_io(n: int) -> None:
    d = stage_dir(n)
    for sub in ("inputs", "outputs"):
        p = d / sub
        if p.exists():
            shutil.rmtree(p)
        p.mkdir(parents=True)


def log_event(event: dict) -> None:
    event["ts"] = datetime.now(timezone.utc).isoformat()
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
    print(json.dumps(event), flush=True)


def load_scenes() -> list[dict]:
    data = json.loads(SCENES_MANIFEST_PATH.read_text(encoding="utf-8"))
    return sorted(data["scenes"], key=lambda s: s["order"])


def parse_first_token(option: str) -> str:
    """Options are formatted as '<id> (...)' - the id is always the first
    whitespace-delimited token (see Stage 05/09's NeedsInputItem construction)."""
    return option.split(" ", 1)[0]


def resolve_close_score_tiebreaks(run_config: dict, out_dir: Path, decisions_log: list) -> "StageResponse":
    main05 = stage_main(5)
    resp = main05(stage_dir(5) / "inputs", out_dir, run_config)
    if resp.status != StageStatus.NEEDS_INPUT:
        return resp
    hitl_decisions: dict[str, str] = {}
    for item in resp.needs_input:
        if item.reason_code != "close_score_tiebreak":
            continue
        m = re.match(r"Beat (\S+):", item.question)
        beat_id = m.group(1) if m else None
        if not beat_id or not item.options:
            continue
        top_candidate_id = parse_first_token(item.options[0])
        hitl_decisions[beat_id] = top_candidate_id
        decisions_log.append(
            {
                "stage": "05_retrieval_verification",
                "decision_point": "close_score_tiebreak",
                "beat_id": beat_id,
                "choice": top_candidate_id,
                "options": item.options,
                "policy": "autonomous default: highest verified CLIP score",
            }
        )
    if not hitl_decisions:
        return resp
    return main05(stage_dir(5) / "inputs", out_dir, run_config, hitl_decisions=hitl_decisions)


def _single_scene_wide_cue_agent_call(beats_path: Path):
    """Deterministic fallback cue-sheet, bypassing the LLM entirely - not a
    guess, this exact remedy (one hand-authored scene-wide cue) is already a
    human-approved precedent in DECISIONS_LOG.md (2026-07-18) for this same
    real failure mode: llama3.2:3b repeatedly producing structurally invalid
    multi-cue boundaries ("genuine model unreliability on multi-cue boundary
    math"). Used only after normal retries are exhausted (see
    resolve_music_stage) - one cue covering every beat, mood_tags drawn from
    the beats' own (already-valid) mood_tags so they're guaranteed within
    Stage 09's allowed_mood_tags without needing to inspect audio_spec.yaml.
    """
    def _call(system_prompt: str, user_message: str) -> str:  # noqa: ARG001
        beats_data = json.loads(beats_path.read_text(encoding="utf-8"))
        ordered = sorted(beats_data.get("beats", []), key=lambda b: b["order"])
        beat_ids = [b["beat_id"] for b in ordered]
        all_moods = sorted({tag for b in ordered for tag in b.get("mood_tags", [])})
        return json.dumps(
            {
                "cues": [
                    {
                        "cue_id": "cue001",
                        "start_beat_id": beat_ids[0],
                        "end_beat_id": beat_ids[-1],
                        "mood_tags": all_moods[:3] or ["quiet"],
                        "target_intensity": 0.5,
                        "rationale": "Deterministic fallback: one scene-wide cue after repeated real multi-cue boundary generation failures.",
                    }
                ]
            }
        )
    return _call


def resolve_music_stage(run_config: dict, out_dir: Path, decisions_log: list) -> "StageResponse":
    """Stage 09's cue-sheet is a fresh (non-deterministic) LLM call on every
    invocation - there's no way to pass a previously-generated cue-sheet back
    in, only a cue_id -> track_ref hitl_decisions map. So a single retry with
    decisions built from attempt 1's cue_ids can still land on track_selection
    again if attempt 2's regenerated cue-sheet doesn't happen to reuse the
    same cue_ids (observed for real). Loop, accumulating decisions across
    attempts, until resolved or a bounded number of attempts is exhausted -
    then fall back to a deterministic single-cue agent_call rather than keep
    retrying an LLM call that has already proven unreliable this run.
    """
    main09 = stage_main(9)
    music_source = GeneratedMusicSource()
    kwargs = dict(music_source=music_source, downloader=generated_audio_downloader)
    hitl_decisions: dict[str, str] = {}
    resp = main09(stage_dir(9) / "inputs", out_dir, run_config, **kwargs)
    for _ in range(5):
        if resp.status != StageStatus.NEEDS_INPUT:
            break
        if any(item.reason_code == "cues_incomplete" for item in resp.needs_input):
            # Not a track-selection problem - only a fresh cue-sheet generation can help.
            resp = main09(stage_dir(9) / "inputs", out_dir, run_config, **kwargs)
            continue
        for item in resp.needs_input:
            if item.reason_code != "track_selection" or not item.options:
                continue
            m = re.match(r"Cue (\S+)", item.question)
            cue_id = m.group(1) if m else None
            if not cue_id or cue_id in hitl_decisions:
                continue
            top_track_ref = parse_first_token(item.options[0])
            hitl_decisions[cue_id] = top_track_ref
            decisions_log.append(
                {
                    "stage": "09_audio_production",
                    "decision_point": "track_selection",
                    "cue_id": cue_id,
                    "choice": top_track_ref,
                    "options": item.options,
                    "policy": "autonomous default: first candidate from GeneratedMusicSource shortlist",
                }
            )
        resp = main09(
            stage_dir(9) / "inputs", out_dir, run_config,
            hitl_decisions=dict(hitl_decisions), selected_by="claude_autonomous_policy", **kwargs,
        )

    if resp.status == StageStatus.NEEDS_INPUT and any(item.reason_code == "cues_incomplete" for item in resp.needs_input):
        decisions_log.append(
            {
                "stage": "09_audio_production",
                "decision_point": "cues_incomplete",
                "policy": "5 real cue-sheet generation attempts all failed structurally (invalid/incomplete beat ranges) - "
                "fell back to a deterministic single scene-wide cue (matches the human-approved 2026-07-18 precedent "
                "in DECISIONS_LOG.md for this same failure mode) instead of continuing to retry an unreliable LLM call",
            }
        )
        beats_path = stage_dir(9) / "inputs" / "beats.json"
        fallback_kwargs = dict(kwargs, agent_call=_single_scene_wide_cue_agent_call(beats_path))
        resp = main09(stage_dir(9) / "inputs", out_dir, run_config, **fallback_kwargs)
        if resp.status == StageStatus.NEEDS_INPUT:
            hitl_decisions = {}
            for item in resp.needs_input:
                if item.reason_code != "track_selection" or not item.options:
                    continue
                m = re.match(r"Cue (\S+)", item.question)
                cue_id = m.group(1) if m else None
                if not cue_id:
                    continue
                top_track_ref = parse_first_token(item.options[0])
                hitl_decisions[cue_id] = top_track_ref
                decisions_log.append(
                    {
                        "stage": "09_audio_production",
                        "decision_point": "track_selection",
                        "cue_id": cue_id,
                        "choice": top_track_ref,
                        "options": item.options,
                        "policy": "autonomous default: first candidate from GeneratedMusicSource shortlist",
                    }
                )
            resp = main09(
                stage_dir(9) / "inputs", out_dir, run_config,
                hitl_decisions=hitl_decisions, selected_by="claude_autonomous_policy", **fallback_kwargs,
            )
    return resp


def merge_assets_manifests(paths: list[Path], run_id: str, scene_id: str) -> dict:
    assets: list = []
    for p in paths:
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            assets.extend(data.get("assets", []))
    return {"run_id": run_id, "scene_id": scene_id, "assets": assets}


def extend_undersized_assets_for_narration(
    fallback_items: list,
    beats_by_id: dict,
    audio_mix: dict,
    merged_assets: dict,
    edit_plan: dict,
    run_id: str,
    decisions_log: list,
) -> bool:
    """Stage 08 flags asset_too_short_for_narration and refuses to build ANY
    timeline at all rather than proceed partially (CLAUDE.md's "no match is a
    routed outcome" principle, enforced as a hard block here) - real footage
    clips are typically a few seconds long, but this pipeline's beats (~4
    sentences of narration each) routinely need 20-40s+ of coverage. Rather
    than loop/stretch real footage (explicitly forbidden), regenerate a
    fresh, sufficiently-long text card for exactly the affected beat(s) -
    the same fix already applied to Stage 06's own fallback lane (see
    config/text_card.yaml's min_duration_s comment) for beats with no
    matched footage at all, extended here to beats that DID get real footage
    but not enough of it. Mutates merged_assets/edit_plan in place. Returns
    True if anything was changed (i.e. Stage 08 is worth retrying).
    """
    narration_duration_by_beat = {s["beat_id"]: s["duration_s"] for s in audio_mix.get("narration_stems", [])}
    text_card_cfg = yaml.safe_load((REPO_ROOT / "config" / "text_card.yaml").read_text(encoding="utf-8"))
    videos_dir = REPO_ROOT / f"shared/runs/{run_id}/cache/generated_videos"
    beats_by_plan_id = {b["beat_id"]: b for b in edit_plan.get("beats", [])}
    changed = False

    for item in fallback_items:
        if item.reason_code != "asset_too_short_for_narration":
            continue
        beat_id = item.item_id
        needed = narration_duration_by_beat.get(beat_id)
        beat = beats_by_id.get(beat_id)
        plan_beat = beats_by_plan_id.get(beat_id)
        if needed is None or beat is None or plan_beat is None:
            continue

        duration_s = max(needed + 2.0, text_card_cfg["min_duration_s"])
        asset_id = f"{beat_id}_narration_extended"
        video_path = videos_dir / f"{beat_id}_narration_extended.mp4"
        generate_text_card(
            text=beat["visual_description"],
            duration_s=duration_s,
            dest_path=video_path,
            width=text_card_cfg["width"],
            height=text_card_cfg["height"],
            fps=text_card_cfg["fps"],
            bg_color=text_card_cfg["bg_color"],
            text_color=text_card_cfg["text_color"],
            font_path=text_card_cfg["font_path"],
            font_size=text_card_cfg["font_size"],
            max_chars_per_line=text_card_cfg["max_chars_per_line"],
        )

        merged_assets["assets"] = [a for a in merged_assets["assets"] if a["beat_id"] != beat_id]
        merged_assets["assets"].append(
            {
                "beat_id": beat_id,
                "asset_id": asset_id,
                "origin": "generated_fallback",
                "file_ref": f"shared/runs/{run_id}/cache/generated_videos/{beat_id}_narration_extended.mp4",
                "duration_s": duration_s,
                "license": "Generated (text card) - no license required",
                "attribution": {"source": "generated", "creator_required": False},
            }
        )

        plan_beat["asset_id"] = asset_id
        plan_beat["shots"] = [
            {"shot_id": f"{beat_id}_s1_extended", "in_s": 0.0, "out_s": duration_s, "hold_duration_s": min(needed, duration_s)}
        ]
        changed = True
        decisions_log.append(
            {
                "stage": "08_timeline_builder",
                "decision_point": "asset_too_short_for_narration",
                "beat_id": beat_id,
                "policy": f"regenerated a {duration_s:.1f}s text card (needed {needed:.1f}s) replacing the too-short real footage, "
                "matching the existing text_card.yaml min_duration_s precedent for the no-footage-at-all case",
            }
        )
    return changed


def clear_audio_cache(run_id: str) -> None:
    """Stage 09 caches narration by beat_id filename and cue tracks by cue_id
    filename (`if not path.exists(): synthesize`) - a reasonable design for a
    single run, but beat_id is scene-prefixed while cue_id is NOT (observed
    for real: cue_id is just "cue001", "cue002", ...), and this orchestrator
    re-runs Stage 09 for the same scene_id multiple times across smoke-test
    attempts and processes multiple scenes under one shared run_id. Without
    this, a stale cue001.mp3 from one scene (or from an earlier, since-fixed
    attempt at the same scene) gets silently reused for an unrelated cue -
    caught for real when beat b001's narration stem, cached from the
    pre-paragraph-fix attempt (the whole chapter collapsed into one
    "paragraph" and got synthesized whole, 818s), was silently reused after
    the real fix - see DECISIONS_LOG.md. Cleared once per scene, before that
    scene's own Stage 09 call(s), not per-attempt inside the retry loop.
    """
    run_cache = REPO_ROOT / f"shared/runs/{run_id}/cache"
    for sub in ("narration", "music"):
        d = run_cache / sub
        if d.exists():
            shutil.rmtree(d)


def run_scene(scene: dict, run_config: dict, reuse_beats: Path | None = None) -> dict:
    scene_id = scene["scene_id"]
    scene_txt_src = REPO_ROOT / "stages/01_manuscript_ingestion/outputs" / f"{scene_id}.txt"
    decisions: list = []
    result = {"scene_id": scene_id, "decisions": decisions, "stages": {}}

    def record(n: int, resp) -> None:
        result["stages"][n] = {
            "status": resp.status.value if hasattr(resp.status, "value") else resp.status,
            "summary": resp.summary,
            "needs_input": [i.to_dict() for i in resp.needs_input],
            "fallback_routed": [i.to_dict() for i in resp.fallback_routed],
            "error": resp.error.to_dict() if resp.error else None,
        }
        log_event({"scene_id": scene_id, "stage": n, **result["stages"][n]})

    # --- Stage 02 ---
    if reuse_beats and reuse_beats.exists():
        beats_path = reuse_beats
        log_event({"scene_id": scene_id, "stage": 2, "status": "REUSED", "summary": f"Reused existing {reuse_beats}"})
    else:
        clean_io(2)
        shutil.copy(scene_txt_src, stage_dir(2) / "inputs" / f"{scene_id}.txt")
        resp02 = stage_main(2)(stage_dir(2) / "inputs", stage_dir(2) / "outputs", run_config)
        record(2, resp02)
        if resp02.status != StageStatus.COMPLETE:
            result["halted_at"] = 2
            return result
        beats_path = stage_dir(2) / "outputs" / "beats.json"

    # --- Stage 03 ---
    clean_io(3)
    shutil.copy(beats_path, stage_dir(3) / "inputs" / "beats.json")
    resp03 = stage_main(3)(stage_dir(3) / "inputs", stage_dir(3) / "outputs", run_config)
    record(3, resp03)
    if resp03.status not in (StageStatus.COMPLETE, StageStatus.NEEDS_INPUT):
        result["halted_at"] = 3
        return result
    candidates_path = stage_dir(3) / "outputs" / "candidates.json"
    if not candidates_path.exists():
        result["halted_at"] = 3
        return result

    # --- Stage 04 ---
    clean_io(4)
    shutil.copy(beats_path, stage_dir(4) / "inputs" / "beats.json")
    shutil.copy(candidates_path, stage_dir(4) / "inputs" / "candidates.json")
    resp04 = stage_main(4)(stage_dir(4) / "inputs", stage_dir(4) / "outputs", run_config)
    record(4, resp04)
    if resp04.status != StageStatus.COMPLETE:
        result["halted_at"] = 4
        return result
    ranked_candidates_path = stage_dir(4) / "outputs" / "candidates.json"

    # --- Stage 05 (autonomous tie-break policy) ---
    clean_io(5)
    shutil.copy(beats_path, stage_dir(5) / "inputs" / "beats.json")
    shutil.copy(ranked_candidates_path, stage_dir(5) / "inputs" / "candidates.json")
    resp05 = resolve_close_score_tiebreaks(run_config, stage_dir(5) / "outputs", decisions)
    record(5, resp05)
    assets_05_path = stage_dir(5) / "outputs" / "assets_manifest.json"

    # --- Stage 06 (CODE-default; always invoked, no-ops if nothing routed) ---
    clean_io(6)
    shutil.copy(beats_path, stage_dir(6) / "inputs" / "beats.json")
    shutil.copy(ranked_candidates_path, stage_dir(6) / "inputs" / "candidates.json")
    resp06 = stage_main(6)(stage_dir(6) / "inputs", stage_dir(6) / "outputs", run_config)
    record(6, resp06)
    if resp06.status not in (StageStatus.COMPLETE,):
        result["halted_at"] = 6
        return result
    assets_06_path = stage_dir(6) / "outputs" / "assets_manifest.json"

    beats_data = json.loads(beats_path.read_text(encoding="utf-8"))
    merged_assets = merge_assets_manifests([assets_05_path, assets_06_path], run_config["run_id"], beats_data.get("scene_id", scene_id))
    if not merged_assets["assets"]:
        result["halted_at"] = "05/06 (no assets at all)"
        return result

    # --- Stage 07 (CODE-default) ---
    clean_io(7)
    shutil.copy(beats_path, stage_dir(7) / "inputs" / "beats.json")
    (stage_dir(7) / "inputs" / "assets_manifest.json").write_text(json.dumps(merged_assets, indent=2), encoding="utf-8")
    resp07 = stage_main(7)(stage_dir(7) / "inputs", stage_dir(7) / "outputs", run_config)
    record(7, resp07)
    if resp07.status == StageStatus.NEEDS_INPUT:
        for item in resp07.needs_input:
            decisions.append(
                {
                    "stage": "07_editorial_direction",
                    "decision_point": item.reason_code,
                    "detail": item.question,
                    "policy": "autonomous default: approve as-is (matches this run's established precedent for runtime_drift/asset_too_short)",
                }
            )
    if resp07.status not in (StageStatus.COMPLETE, StageStatus.NEEDS_INPUT):
        result["halted_at"] = 7
        return result
    edit_plan_path = stage_dir(7) / "outputs" / "edit_plan.json"
    if not edit_plan_path.exists():
        result["halted_at"] = 7
        return result
    edit_plan = json.loads(edit_plan_path.read_text(encoding="utf-8"))

    # --- Stage 09 (BEFORE 08 - see module docstring) ---
    clean_io(9)
    clear_audio_cache(run_config["run_id"])
    shutil.copy(beats_path, stage_dir(9) / "inputs" / "beats.json")
    shutil.copy(scene_txt_src, stage_dir(9) / "inputs" / "scene_text.txt")
    resp09 = resolve_music_stage(run_config, stage_dir(9) / "outputs", decisions)
    record(9, resp09)
    audio_mix_path = stage_dir(9) / "outputs" / "audio_mix.json"
    scene_mix_path = stage_dir(9) / "outputs" / "scene_mix.wav"
    music_cue_intent_path = stage_dir(9) / "outputs" / "music_cue_intent.json"

    # --- Stage 08 (with audio_mix.json reconciliation if 09 produced one) ---
    clean_io(8)
    shutil.copy(edit_plan_path, stage_dir(8) / "inputs" / "edit_plan.json")
    (stage_dir(8) / "inputs" / "assets_manifest.json").write_text(json.dumps(merged_assets, indent=2), encoding="utf-8")
    if audio_mix_path.exists():
        shutil.copy(audio_mix_path, stage_dir(8) / "inputs" / "audio_mix.json")
    resp08 = stage_main(8)(stage_dir(8) / "inputs", stage_dir(8) / "outputs", run_config)
    record(8, resp08)
    if resp08.status == StageStatus.FALLBACK_ROUTED and audio_mix_path.exists():
        audio_mix = json.loads(audio_mix_path.read_text(encoding="utf-8"))
        beats_by_id = {b["beat_id"]: b for b in beats_data.get("beats", [])}
        changed = extend_undersized_assets_for_narration(
            resp08.fallback_routed, beats_by_id, audio_mix, merged_assets, edit_plan, run_config["run_id"], decisions
        )
        if changed:
            (stage_dir(8) / "inputs" / "assets_manifest.json").write_text(json.dumps(merged_assets, indent=2), encoding="utf-8")
            (stage_dir(8) / "inputs" / "edit_plan.json").write_text(json.dumps(edit_plan, indent=2), encoding="utf-8")
            resp08 = stage_main(8)(stage_dir(8) / "inputs", stage_dir(8) / "outputs", run_config)
            record(8, resp08)
    if resp08.status not in (StageStatus.COMPLETE, StageStatus.FALLBACK_ROUTED):
        result["halted_at"] = 8
        return result
    timeline_path = stage_dir(8) / "outputs" / "timeline.json"
    if not timeline_path.exists():
        result["halted_at"] = 8
        return result

    # --- Stage 10 ---
    clean_io(10)
    shutil.copy(timeline_path, stage_dir(10) / "inputs" / "timeline.json")
    if audio_mix_path.exists():
        shutil.copy(audio_mix_path, stage_dir(10) / "inputs" / "audio_mix.json")
    resp10 = stage_main(10)(stage_dir(10) / "inputs", stage_dir(10) / "outputs", run_config)
    record(10, resp10)
    (stage_dir(10) / "outputs" / "APPROVED.md").write_text(
        f"# Stage 10 - human_review_gate - APPROVED\n\n"
        f"Auto-approved under the human-authorized 'autonomous with summary review' policy "
        f"for this full-novel bulk run (scene {scene_id}). Every judgment call this policy made "
        f"for this scene is itemized in this run's consolidated summary, not individually approved here.\n\n"
        f"**Timestamp:** {datetime.now(timezone.utc).isoformat()}\n",
        encoding="utf-8",
    )

    # --- Stage 11 ---
    clean_io(11)
    shutil.copy(timeline_path, stage_dir(11) / "inputs" / "timeline.json")
    if scene_mix_path.exists():
        shutil.copy(scene_mix_path, stage_dir(11) / "inputs" / "scene_mix.wav")
    resp11 = stage_main(11)(stage_dir(11) / "inputs", stage_dir(11) / "outputs", run_config)
    record(11, resp11)
    final_mp4_path = stage_dir(11) / "outputs" / "final.mp4"
    if resp11.status != StageStatus.COMPLETE or not final_mp4_path.exists():
        result["halted_at"] = 11
        return result

    # --- Stage 12 ---
    clean_io(12)
    for name, path in [
        ("beats.json", beats_path),
        ("candidates.json", ranked_candidates_path),
        ("edit_plan.json", edit_plan_path),
        ("timeline.json", timeline_path),
        ("final.mp4", final_mp4_path),
    ]:
        shutil.copy(path, stage_dir(12) / "inputs" / name)
    (stage_dir(12) / "inputs" / "assets_manifest.json").write_text(json.dumps(merged_assets, indent=2), encoding="utf-8")
    if music_cue_intent_path.exists():
        shutil.copy(music_cue_intent_path, stage_dir(12) / "inputs" / "music_cue_intent.json")
    if audio_mix_path.exists():
        shutil.copy(audio_mix_path, stage_dir(12) / "inputs" / "audio_mix.json")
    manifest_path = REPO_ROOT / f"shared/runs/{run_config['run_id']}/manifest.json"
    if manifest_path.exists():
        shutil.copy(manifest_path, stage_dir(12) / "inputs" / "manifest.json")
    resp12 = stage_main(12)(stage_dir(12) / "inputs", stage_dir(12) / "outputs", run_config)
    record(12, resp12)
    if resp12.status == StageStatus.FAILED:
        decisions.append(
            {
                "stage": "12_qa_attribution",
                "decision_point": "qa_report_failed",
                "detail": resp12.error.message if resp12.error else "qa_report.pass was False",
                "policy": "logged for review; proceeding to Stage 13 anyway (bulk-run policy) since 12 never blocks re-running an upstream stage and the video itself is real",
            }
        )

    # --- Stage 13 ---
    clean_io(13)
    shutil.copy(final_mp4_path, stage_dir(13) / "inputs" / "final.mp4")
    resp13 = stage_main(13)(stage_dir(13) / "inputs", stage_dir(13) / "outputs", run_config)
    record(13, resp13)
    final_pixel_path = stage_dir(13) / "outputs" / "final_pixel_art.mp4"

    # --- Archive this scene's final artifacts before the next scene's clean_io() wipes them ---
    archive_dir = REPO_ROOT / f"shared/runs/{run_config['run_id']}/chapter_outputs/{scene_id}"
    archive_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(final_mp4_path, archive_dir / "final_raw.mp4")
    if final_pixel_path.exists():
        shutil.copy(final_pixel_path, archive_dir / "final_styled.mp4")
    result["final_video"] = str(archive_dir / ("final_styled.mp4" if final_pixel_path.exists() else "final_raw.mp4"))
    result["completed"] = True
    return result


def main(scene_ids: list[str] | None = None) -> int:
    run_config = yaml.safe_load(RUN_CONFIG_PATH.read_text(encoding="utf-8"))
    scenes = load_scenes()
    if scene_ids:
        scenes = [s for s in scenes if s["scene_id"] in scene_ids]

    existing_ch1_beats = stage_dir(2) / "outputs" / "beats.json"
    ch1_beats_backup = REPO_ROOT / f"shared/runs/{run_config['run_id']}/ch1_sc1_beats_backup.json"
    if existing_ch1_beats.exists() and not ch1_beats_backup.exists():
        ch1_beats_backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(existing_ch1_beats, ch1_beats_backup)

    all_results = []
    for scene in scenes:
        reuse = ch1_beats_backup if scene["scene_id"] == "ch1_sc1" and ch1_beats_backup.exists() else None
        log_event({"scene_id": scene["scene_id"], "event": "SCENE_START"})
        try:
            res = run_scene(scene, run_config, reuse_beats=reuse)
        except Exception as exc:  # noqa: BLE001 - a bulk run must not die on one scene's crash
            res = {"scene_id": scene["scene_id"], "crashed": True, "error": str(exc), "traceback": traceback.format_exc()}
            log_event({"scene_id": scene["scene_id"], "event": "SCENE_CRASHED", "error": str(exc)})
        all_results.append(res)
        log_event({"scene_id": scene["scene_id"], "event": "SCENE_DONE", "completed": res.get("completed", False)})

    summary_path = REPO_ROOT / f"shared/runs/{run_config['run_id']}/full_novel_summary.json"
    summary_path.write_text(json.dumps(all_results, indent=2), encoding="utf-8")
    log_event({"event": "RUN_DONE", "summary_path": str(summary_path)})
    return 0


if __name__ == "__main__":
    scene_arg = sys.argv[1:] if len(sys.argv) > 1 else None
    sys.exit(main(scene_arg))
