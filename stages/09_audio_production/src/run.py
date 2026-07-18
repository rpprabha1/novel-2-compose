"""Stage 09: audio_production.

Agent half (music cue sheet: boundaries, mood, intensity - see
AGENT_PROMPT.md) + code half (narration TTS from the source manuscript's own
prose, music search/fetch, ducking, crossfades, loudness normalization).
HYBRID per CLAUDE.md section 2/4. The agent never picks a final track -
track selection is always a mandatory human decision.

Timing is audio-driven: narration_stems are placed sequentially by their
actual synthesized duration, not constrained to edit_plan.json's visual
hold_duration_s (reading a beat's full paragraph aloud routinely takes far
longer than its on-screen hold). This stage does not read edit_plan.json at
all - only beats.json (for order + text) and the scene's source text.
08_timeline_builder's timeline.json must be regenerated with each beat's
hold stretched to at least its narration duration before 11_assembly_render
can use it. See ARCHITECTURE.md 2026-07-15 / DECISIONS_LOG.md.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Callable

import requests
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from shared.agents import AgentBackendError, call_ollama, load_agent_config, resolve_model  # noqa: E402
from shared.envelopes import (  # noqa: E402
    ErrorInfo,
    FallbackRoutedItem,
    NeedsInputItem,
    StageResponse,
    StageStatus,
    validate_against_schema,
)
from shared.generation import TTSError, synthesize_speech  # noqa: E402
from shared.manifest import append_manifest_entries  # noqa: E402
from shared.media import (  # noqa: E402
    FFmpegError,
    apply_ducking,
    crossfade_concat,
    normalize_loudness,
    overlay_narration,
    probe_duration_s,
    trim_audio,
)
from shared.sources import MusicSource  # noqa: E402

STAGE_NAME = "09_audio_production"
PROMPT_PATH = Path(__file__).resolve().parents[1] / "AGENT_PROMPT.md"
_INCLUDED_SECTION_NUMBERS = {"1", "2", "3", "4", "5", "6", "9"}

AgentCallFn = Callable[[str, str], str]
TTSFn = Callable[[str, Path], None]


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


def _default_tts(text: str, dest_path: Path) -> None:
    cfg = yaml.safe_load((REPO_ROOT / "config" / "tts.yaml").read_text(encoding="utf-8"))
    synthesize_speech(
        text=text,
        dest_path=dest_path,
        model_path=REPO_ROOT / cfg["model_path"],
        config_path=REPO_ROOT / cfg["config_path"],
        length_scale=cfg.get("length_scale", 1.0),
    )


def _default_downloader(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.get(url, timeout=60, stream=True)
    resp.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 16):
            f.write(chunk)


def _strip_wrapper(raw_text: str) -> str:
    text = raw_text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence_match:
        return fence_match.group(1)
    brace_match = re.search(r"(\{.*\})", text, re.DOTALL)
    if brace_match:
        return brace_match.group(1)
    return text


def _extract_excerpt(scene_text: str, text_excerpt_ref: str) -> str:
    paragraphs = [p for p in scene_text.strip().split("\n\n") if p.strip()]
    match = re.match(r"para:(\d+)(?:-(\d+))?", text_excerpt_ref)
    if not match:
        return ""
    start = int(match.group(1))
    end = int(match.group(2)) if match.group(2) else start
    return " ".join(paragraphs[start - 1 : end])


def _beat_range(start_id: str, end_id: str, ordered_ids: list[str]) -> list[str] | None:
    if start_id not in ordered_ids or end_id not in ordered_ids:
        return None
    start_idx, end_idx = ordered_ids.index(start_id), ordered_ids.index(end_id)
    if start_idx > end_idx:
        return None
    return ordered_ids[start_idx : end_idx + 1]


def _needs_input(run_id: str, reason_code: str, question: str, options: list[str]) -> NeedsInputItem:
    return NeedsInputItem(reason_code=reason_code, question=question, options=options)


def main(
    input_dir: Path,
    output_dir: Path,
    run_config: dict,
    agent_call: AgentCallFn | None = None,
    music_source: MusicSource | None = None,
    tts_fn: TTSFn | None = None,
    downloader: Callable[[str, Path], None] | None = None,
    audio_spec: dict | None = None,
    hitl_decisions: dict[str, str] | None = None,
    selected_by: str = "human",
) -> StageResponse:
    run_id = run_config["run_id"]
    beats_path = input_dir / "beats.json"
    scene_text_path = input_dir / "scene_text.txt"

    missing = [p.name for p in (beats_path, scene_text_path) if not p.exists()]
    if missing:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(message=f"Missing required input file(s) in {input_dir}: {missing}"),
        )

    beats_data = json.loads(beats_path.read_text(encoding="utf-8"))
    scene_text = scene_text_path.read_text(encoding="utf-8")
    beats_by_id = {b["beat_id"]: b for b in beats_data.get("beats", [])}
    ordered_beat_ids = [b["beat_id"] for b in sorted(beats_data.get("beats", []), key=lambda b: b["order"])]

    if not ordered_beat_ids:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(message="beats.json has no beats - nothing to score."),
        )

    audio_spec = audio_spec or yaml.safe_load((REPO_ROOT / "config" / "audio_spec.yaml").read_text(encoding="utf-8"))
    tone = run_config.get("tone", "")
    # Union of the tone's allowed list and whatever moods this run's own beats
    # actually carry (audio_spec.yaml's own comment documents this union -
    # the tone list alone was a real bug: a beat legitimately tagged outside
    # the tone's list, e.g. "romantic" beats in a gothic-suspense scene,
    # made every cue covering it schema-unsatisfiable regardless of model
    # quality, since the agent is shown that beat's real mood_tags but
    # validated against a narrower set it was never told to stay within).
    beat_mood_tags = {tag for bid in ordered_beat_ids for tag in beats_by_id[bid].get("mood_tags", [])}
    allowed_mood_tags = set(audio_spec["tone_music_tags"].get(tone, [])) | beat_mood_tags
    target_lufs = audio_spec["loudness"]["target_lufs"]
    ducking_depth_db = audio_spec["ducking"]["depth_db"]
    ducking_attack_ms = audio_spec["ducking"]["attack_ms"]
    crossfade_len = audio_spec["crossfade"]["default_length_s"]

    # --- Narration: source manuscript's own prose, verbatim per beat, via TTS ---
    # Timing is audio-driven, NOT constrained to edit_plan.json's visual
    # hold_duration_s: reading a beat's full paragraph aloud routinely takes
    # far longer than its on-screen hold (observed for real: 8-15s of
    # narration against 2.75-3.75s visual holds). Stems are placed
    # sequentially back-to-back with zero overlap, using each stem's actual
    # synthesized duration; the resulting (longer) total becomes this
    # scene's true runtime. Stage 08's timeline.json - built from the
    # visual-only hold_duration_s - is now stale and must be regenerated
    # with each beat's hold stretched to at least its narration_stems
    # duration before 11_assembly_render can use it. See ARCHITECTURE.md
    # 2026-07-15 and DECISIONS_LOG.md for the human decision behind this.
    tts_fn = tts_fn or _default_tts
    narration_dir = REPO_ROOT / "shared" / "runs" / run_id / "cache" / "narration"
    narration_stems: list[dict] = []
    tts_failures: list[str] = []
    audio_cursor = 0.0
    for beat_id in ordered_beat_ids:
        beat = beats_by_id.get(beat_id)
        excerpt = _extract_excerpt(scene_text, beat["text_excerpt_ref"])
        if not excerpt:
            tts_failures.append(f"{beat_id}: could not resolve text_excerpt_ref={beat['text_excerpt_ref']!r}")
            continue
        stem_path = narration_dir / f"{beat_id}.wav"
        try:
            if not stem_path.exists():
                tts_fn(excerpt, stem_path)
            duration = probe_duration_s(stem_path)
        except (TTSError, FFmpegError) as exc:
            tts_failures.append(f"{beat_id}: {exc}")
            continue
        start_s = audio_cursor
        audio_cursor += duration
        narration_stems.append(
            {
                "beat_id": beat_id,
                "file_ref": f"shared/runs/{run_id}/cache/narration/{beat_id}.wav",
                "start_s": round(start_s, 4),
                "duration_s": round(duration, 4),
                "_local_path": stem_path,
            }
        )
    beat_offsets = {s["beat_id"]: (s["start_s"], s["start_s"] + s["duration_s"]) for s in narration_stems}

    if tts_failures:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(message=f"{len(tts_failures)} narration synthesis failure(s)", diagnostics="; ".join(tts_failures)),
        )

    # --- Agent half: music cue sheet ---
    beats_payload = [
        {"beat_id": bid, "mood_tags": beats_by_id[bid].get("mood_tags", []), "order": beats_by_id[bid]["order"]}
        for bid in ordered_beat_ids
    ]
    agent_call = agent_call or _default_agent_call
    system_prompt = _render_system_prompt(PROMPT_PATH.read_text(encoding="utf-8"))
    user_message = (
        f"tone: {tone}\n"
        f"music_intensity_curve: {run_config.get('music_intensity_curve', 'flat')}\n"
        f"allowed_mood_tags: {sorted(allowed_mood_tags)}\n\n"
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
        intent = json.loads(_strip_wrapper(raw_response))
    except json.JSONDecodeError as exc:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.NEEDS_INPUT,
            needs_input=[_needs_input(run_id, "cues_incomplete", f"Invalid JSON from the cue-sheet model: {exc}. Retry?", ["Retry generation"])],
        )

    intent["run_id"] = run_id
    intent["scene_id"] = beats_data.get("scene_id", "")
    cues = intent.get("cues") or []

    covered: set[str] = set()
    coverage_errors: list[str] = []
    for cue in cues:
        bad_tags = set(cue.get("mood_tags", [])) - allowed_mood_tags
        if bad_tags:
            coverage_errors.append(f"{cue.get('cue_id')}: mood tags {bad_tags} not in allowed_mood_tags")
        rng = _beat_range(cue.get("start_beat_id"), cue.get("end_beat_id"), ordered_beat_ids)
        if rng is None:
            coverage_errors.append(f"{cue.get('cue_id')}: invalid start/end beat range")
            continue
        overlap = covered & set(rng)
        if overlap:
            coverage_errors.append(f"{cue.get('cue_id')}: overlaps already-covered beat(s) {overlap}")
        covered.update(rng)

    missing_beats = set(ordered_beat_ids) - covered

    # Auto-repair a specific, safe gap pattern: a single cue that leaves only
    # a leading prefix of beats uncovered (observed for real - llama3.2:3b
    # reliably starts the cue where it perceives a mood "shift" rather than
    # at the scene's actual start). With only one cue, there's nowhere else
    # for those beats to go, so extending it backward isn't a creative
    # choice - it's the only coherent repair. Multiple cues or a gap in the
    # middle are genuinely ambiguous and still block on NEEDS_INPUT.
    cue_gap_repair: str | None = None
    if len(cues) == 1 and not coverage_errors and missing_beats:
        cue = cues[0]
        start_id = cue.get("start_beat_id")
        if start_id in ordered_beat_ids:
            start_idx = ordered_beat_ids.index(start_id)
            leading_prefix = set(ordered_beat_ids[:start_idx])
            if leading_prefix and leading_prefix == missing_beats:
                cue_gap_repair = (
                    f"{cue['cue_id']}: start_beat_id {start_id!r} -> {ordered_beat_ids[0]!r} "
                    f"(model left leading beat(s) {sorted(leading_prefix)} uncovered)"
                )
                cue["start_beat_id"] = ordered_beat_ids[0]
                covered.update(leading_prefix)
                missing_beats = set()

    if missing_beats or coverage_errors:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.NEEDS_INPUT,
            needs_input=[
                _needs_input(
                    run_id,
                    "cues_incomplete",
                    f"Cue sheet doesn't cleanly cover every beat. Missing: {sorted(missing_beats)}. Errors: {coverage_errors}. Retry?",
                    ["Retry generation", "Manually correct cue boundaries"],
                )
            ],
        )

    try:
        validate_against_schema(intent, "music_cue_intent.schema.json")
    except Exception as exc:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(message="Music cue intent failed schema validation", diagnostics=str(exc)),
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "music_cue_intent.json").write_text(json.dumps(intent, indent=2), encoding="utf-8")

    # --- Code half: music search + mandatory HITL track selection ---
    if music_source is None:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.NEEDS_INPUT,
            output_manifest=["outputs/music_cue_intent.json"],
            needs_input=[
                _needs_input(
                    run_id,
                    "no_music_source_configured",
                    "No MusicSource was provided. No approved source has a public search API yet "
                    "(see LICENSES.md) - pass a ManualMusicSource built from curated candidates.",
                    ["Provide a ManualMusicSource"],
                )
            ],
        )

    hitl_decisions = hitl_decisions or {}
    downloader = downloader or _default_downloader
    music_cache_dir = REPO_ROOT / "shared" / "runs" / run_id / "cache" / "music"

    music_stems: list[dict] = []
    tiebreak_items: list[NeedsInputItem] = []
    fallback_items: list[FallbackRoutedItem] = []

    for cue in cues:
        candidates = music_source.search(cue["mood_tags"], max_results=3)
        if not candidates:
            fallback_items.append(
                FallbackRoutedItem(
                    item_id=cue["cue_id"], reason_code="no_music_candidates", detail=f"No candidates found for tags {cue['mood_tags']}"
                )
            )
            continue

        if cue["cue_id"] not in hitl_decisions:
            tiebreak_items.append(
                _needs_input(
                    run_id,
                    "track_selection",
                    f"Cue {cue['cue_id']} ({cue['mood_tags']}): pick a track.",
                    [f"{c.track_ref} ({c.source}, {c.license}, {c.url})" for c in candidates],
                )
            )
            continue

        chosen_ref = hitl_decisions[cue["cue_id"]]
        chosen = next((c for c in candidates if c.track_ref == chosen_ref), None)
        if chosen is None:
            return StageResponse(
                envelope_id="",
                run_id=run_id,
                stage=STAGE_NAME,
                status=StageStatus.FAILED,
                error=ErrorInfo(message=f"hitl_decisions references track_ref {chosen_ref!r} not among cue {cue['cue_id']!r}'s candidates"),
            )

        cue_start_s, _ = beat_offsets[cue["start_beat_id"]]
        _, cue_end_s = beat_offsets[cue["end_beat_id"]]
        cue_duration = cue_end_s - cue_start_s

        raw_path = music_cache_dir / f"{chosen.track_ref}_raw{Path(chosen.download_url or '').suffix or '.mp3'}"
        trimmed_path = music_cache_dir / f"{cue['cue_id']}.mp3"
        try:
            if chosen.download_url and not raw_path.exists():
                downloader(chosen.download_url, raw_path)
            if not trimmed_path.exists():
                trim_audio(raw_path, cue_duration, trimmed_path)
        except (requests.RequestException, FFmpegError) as exc:
            fallback_items.append(
                FallbackRoutedItem(item_id=cue["cue_id"], reason_code="music_fetch_failed", detail=str(exc))
            )
            continue

        music_stems.append(
            {
                "cue_id": cue["cue_id"],
                "track_ref": chosen.track_ref,
                "file_ref": f"shared/runs/{run_id}/cache/music/{cue['cue_id']}.mp3",
                "start_s": round(cue_start_s, 4),
                "duration_s": round(cue_duration, 4),
                "selected_by": selected_by,
                "license": chosen.license,
                "source": chosen.source,
                "requires_attribution": chosen.requires_attribution,
                "creator": chosen.creator,
                "_local_path": trimmed_path,
            }
        )

    if tiebreak_items:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.NEEDS_INPUT,
            output_manifest=["outputs/music_cue_intent.json"],
            needs_input=tiebreak_items,
            fallback_routed=fallback_items,
        )

    if not music_stems:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FALLBACK_ROUTED,
            output_manifest=["outputs/music_cue_intent.json"],
            fallback_routed=fallback_items,
        )

    # --- Mixing: crossfade cues -> duck under narration -> overlay narration -> normalize ---
    mix_cache_dir = REPO_ROOT / "shared" / "runs" / run_id / "cache" / "mix"
    ordered_music_paths = [stem["_local_path"] for stem in music_stems]
    concatenated_path = mix_cache_dir / "concatenated_music.wav"
    ducked_path = mix_cache_dir / "ducked_music.wav"
    mixed_path = mix_cache_dir / "mixed.wav"
    final_path = output_dir / "scene_mix.wav"

    try:
        crossfade_concat(ordered_music_paths, crossfade_len, concatenated_path)
        narration_windows = [(s["start_s"], s["start_s"] + s["duration_s"]) for s in narration_stems]
        apply_ducking(concatenated_path, narration_windows, ducking_depth_db, ducked_path)
        narration_pairs = [(s["_local_path"], s["start_s"]) for s in narration_stems]
        overlay_narration(ducked_path, narration_pairs, mixed_path)
        achieved_lufs = normalize_loudness(mixed_path, target_lufs, final_path)
    except FFmpegError as exc:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(message="Audio mixing failed", diagnostics=str(exc)),
        )

    for i, stem in enumerate(music_stems):
        stem["crossfade_in_s"] = 0.0 if i == 0 else crossfade_len
        stem["crossfade_out_s"] = 0.0 if i == len(music_stems) - 1 else crossfade_len

    output = {
        "run_id": run_id,
        "scene_id": intent["scene_id"],
        "narration_stems": [
            {"beat_id": s["beat_id"], "file_ref": s["file_ref"], "start_s": s["start_s"], "duration_s": s["duration_s"]}
            for s in narration_stems
        ],
        "music_stems": [
            {
                "cue_id": s["cue_id"],
                "track_ref": s["track_ref"],
                "file_ref": s["file_ref"],
                "start_s": s["start_s"],
                "duration_s": s["duration_s"],
                "selected_by": s["selected_by"],
                "crossfade_in_s": s["crossfade_in_s"],
                "crossfade_out_s": s["crossfade_out_s"],
            }
            for s in music_stems
        ],
        "mix_params": {"ducking_depth_db": ducking_depth_db, "ducking_attack_ms": ducking_attack_ms},
        "final_lufs": round(achieved_lufs, 2),
        "total_duration_s": round(audio_cursor, 4),
    }
    validate_against_schema(output, "audio_mix.schema.json")
    (output_dir / "audio_mix.json").write_text(json.dumps(output, indent=2), encoding="utf-8")

    now_manifest_entries = [
        {
            "entry_id": s["track_ref"],
            "kind": "music",
            "fetched_by_stage": STAGE_NAME,
            "fetched_at": "",
            "source": s["source"],
            "license": s["license"],
            "attribution_required": s["requires_attribution"],
            **({"creator": s["creator"]} if s.get("creator") else {}),
        }
        for s in music_stems
    ]
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    for entry in now_manifest_entries:
        entry["fetched_at"] = now
    append_manifest_entries(REPO_ROOT / "shared" / "runs" / run_id, run_id, now_manifest_entries)

    summary = (
        f"Synthesized {len(narration_stems)} narration stem(s), mixed {len(music_stems)} music cue(s), "
        f"final_lufs={output['final_lufs']} (target {target_lufs})."
    )
    if cue_gap_repair:
        summary += f" Auto-repaired a cue coverage gap: {cue_gap_repair}."
    if fallback_items:
        summary += f" {len(fallback_items)} cue(s) routed to fallback (no candidates or fetch failure)."

    return StageResponse(
        envelope_id="",
        run_id=run_id,
        stage=STAGE_NAME,
        status=StageStatus.FALLBACK_ROUTED if fallback_items else StageStatus.COMPLETE,
        summary=summary,
        output_manifest=["outputs/music_cue_intent.json", "outputs/audio_mix.json", "outputs/scene_mix.wav"],
        fallback_routed=fallback_items,
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
