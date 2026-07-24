#!/usr/bin/env python3
"""Stage 00: coordinator.

The hub-and-spoke Coordinator (CLAUDE.md sections 2/5). This is the ONLY
component that invokes stages, moves data between them, enforces gates, and
holds run-level state. It plays the Coordinator role for a run spanning all of
a novel's scenes in one pass.

Two layers, per CLAUDE.md section 2:
  * CODE core (the `Coordinator` class): Task Envelope construction, ordered
    stage invocation, schema validation of every Stage Response against its
    `expected_output_schema`, gate enforcement (refuses Stage N+1 without
    `outputs/APPROVED.md` on the human-review gate), and append-only logging of
    every envelope + response to `shared/runs/<run_id>/coordinator_log.jsonl`.
  * Agent-shaped work (framing NEEDS_INPUT / HITL for the human): under this
    bulk-run's human-authorized "autonomous with summary review" policy
    (2026-07-21/22, see DECISIONS_LOG.md), the music track-selection HITL is
    resolved by a documented default policy in `resolve_music_stage()` and every
    choice logged for a consolidated after-the-fact summary rather than a live
    per-stage prompt.

Footage comes from the SOURCE-FREE DOWNLOADER LANE (2026-07-23 cutover, author
override): per scene, 01_1_downloader is auto-invoked once per unique beat/shot
query (treated as an opaque black box - never read/imported), its whole
outputs/ pool is cataloged by shared/downloader_manifest.py, 01_2_scene_scoring
CLIP-ranks the pool per beat, then 09_audio_production synthesizes narration
BEFORE 07_2_narration_shot_mapping physically extracts and tiles shots to the
real narration length (narration length is authoritative - see the 2026-07-18
DECISIONS_LOG.md entries; running audio before final tiling is what prevents the
video freezing against longer narration).

Canonical per-scene order:
  02 -> [01_1 download -> downloader_manifest -> 01_2 scene_scoring]
     -> 09 audio -> 07_2 shot mapping -> 08 timeline -> 10 review gate
     -> 11 assembly -> 12 qa -> 13 pixel art -> 14 anime.

This module is the home of the orchestration; `run_full_novel.py` at the repo
root is a thin CLI shim that delegates here.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Callable
from uuid import uuid4

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from shared.downloader_manifest import build_manifest as build_downloader_manifest  # noqa: E402
from shared.envelopes import (  # noqa: E402
    StageResponse,
    StageStatus,
    TaskEnvelope,
    validate_against_schema,
    validate_stage_response,
    validate_task_envelope,
)
from shared.sources import GeneratedMusicSource, JamendoMusicSource, generated_audio_downloader  # noqa: E402
from shared.text import extract_search_terms  # noqa: E402

# --- Run configuration ---
# NB: the run_config directory name ("animal_farm_ch1") and the run_id value
# stored inside it ("animal_farm_ch1_2026_07_21", used by every stage to build
# its shared/runs/<run_id>/... cache paths) are NOT the same string - this was a
# real bug caught before the first real invocation.
RUN_CONFIG_PATH = REPO_ROOT / "shared/runs/animal_farm_ch1/run_config.yaml"
SCENES_MANIFEST_PATH = REPO_ROOT / "stages/01_manuscript_ingestion/outputs/scenes_manifest.json"

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
    14: "anime_style_conversion",
}

# Per-stage output contract: stage name -> (expected_output_schema, primary
# output file to payload-validate). Stages producing only media (10/11/13/14)
# have no JSON payload contract - only their StageResponse wrapper is validated,
# and their envelope's expected_output_schema is the response schema itself.
STAGE_CONTRACTS: dict[str, tuple[str, str | None]] = {
    "01_manuscript_ingestion": ("scenes_manifest.schema.json", "scenes_manifest.json"),
    "02_1_screenplay": ("screenplay.schema.json", "screenplay.json"),
    "02_2_scene_extraction": ("scenes_manifest.schema.json", "scenes_manifest.json"),
    "02_beat_extraction": ("beats.schema.json", "beats.json"),
    "01_2_scene_scoring": ("scene_scores.schema.json", "scene_scores.json"),
    "09_audio_production": ("audio_mix.schema.json", "audio_mix.json"),
    "07_2_narration_shot_mapping": ("edit_plan.schema.json", "edit_plan.json"),
    "08_timeline_builder": ("timeline.schema.json", "timeline.json"),
    "12_qa_attribution": ("qa_report.schema.json", "qa_report.json"),
}
_NO_PAYLOAD_SCHEMA = "stage_response.schema.json"

# Statuses that still write a valid primary payload worth validating.
_PAYLOAD_OK_STATUSES = (StageStatus.COMPLETE, StageStatus.FALLBACK_ROUTED)


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


def _load_stage_main(stage_path: Path, mod_name: str):
    """Load a stage's main() by path (for stages with non-integer numbers like
    01_2 / 07_2 that can't go through STAGE_NAMES/stage_main())."""
    spec = importlib.util.spec_from_file_location(mod_name, stage_path / "src" / "run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.main


def clean_io(n: int) -> None:
    d = stage_dir(n)
    for sub in ("inputs", "outputs"):
        p = d / sub
        if p.exists():
            shutil.rmtree(p)
        p.mkdir(parents=True)


# --- Downloader lane (footage source since the 2026-07-23 cutover) ------------
DOWNLOADER_DIR = REPO_ROOT / "stages" / "01_1_downloader"
DOWNLOADER_OUTPUTS_DIR = DOWNLOADER_DIR / "outputs"
SCENE_SCORING_DIR = REPO_ROOT / "stages" / "01_2_scene_scoring"
SHOT_MAPPING_DIR = REPO_ROOT / "stages" / "07_2_narration_shot_mapping"
SCREENPLAY_DIR = REPO_ROOT / "stages" / "02_1_screenplay"
SCENE_EXTRACTION_DIR = REPO_ROOT / "stages" / "02_2_scene_extraction"
_DOWNLOADER_TIMEOUT_S = 900

# Front-end (screenplay -> scene extraction) is opt-in per the author's decision
# (default off) so the existing pre-split scene set / mid-flight run is untouched
# until explicitly enabled. When on, it regenerates the scene set from prose via
# 02_1 + 02_2 before the per-scene loop.
MANUSCRIPT_OUTPUTS_DIR = REPO_ROOT / "stages" / "01_manuscript_ingestion" / "outputs"


def _frontend_enabled(run_config: dict) -> bool:
    return bool(run_config.get("screenplay_frontend", False))


def _default_downloader_invoke(query: str) -> None:
    """Auto-invoke the downloader CLI for one query, per downloader_usage.md:
    the one-shot form `python download.py "<query>"` downloads the top matches
    into stages/01_1_downloader/outputs/ and prompts once for quality; we feed
    "3" on stdin (480p, "smaller file" per the usage guide) rather than the
    Best-quality default - a real run showed "Best quality" pulling down
    multi-hundred-MB to multi-GB full videos for what becomes a few seconds of
    beat coverage. Treats the downloader as a documented black box - never reads
    or imports its own code, only runs the CLI and consumes outputs/.

    encoding="utf-8"/errors="replace" are explicit (not just PYTHONIOENCODING in
    the child's env): subprocess.run's OWN stdout/stderr reader threads decode
    using the parent interpreter's locale default (cp1252 on this Windows
    machine) unless told otherwise, independent of what the child does with its
    own encoding - real run hit UnicodeDecodeError in those reader threads on the
    downloader's non-ASCII output otherwise. Downloads are unaffected either way.
    """
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    subprocess.run(
        [sys.executable, "download.py", query],
        cwd=str(DOWNLOADER_DIR),
        input="3\n",
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        env=env,
        timeout=_DOWNLOADER_TIMEOUT_S,
    )


def download_scene_queries(beats: list[dict], scene_id: str, invoke: Callable[[str], None] | None = None) -> list[str]:
    """Auto-invoke the downloader once per UNIQUE beat/shot query (each beat's own
    search_query, falling back to mechanical keyword extraction of its
    visual_description; de-duplicated in first-seen order, since many beats in a
    scene legitimately share one query - e.g. a multi-beat speech - and
    re-invoking the same search repeatedly would be pure waste). This only
    POPULATES the downloader's shared outputs/ pool; the scene then scores
    against the WHOLE pool, not a per-scene subset.

    `invoke` is injectable so tests never run the real downloader. Returns the
    ordered list of unique queries actually issued."""
    invoke = invoke or _default_downloader_invoke

    queries: list[str] = []
    seen: set[str] = set()
    for beat in beats:
        query = (beat.get("search_query") or "").strip() or extract_search_terms(beat["visual_description"])
        if query not in seen:
            seen.add(query)
            queries.append(query)

    for query in queries:
        try:
            invoke(query)
        except Exception as exc:  # noqa: BLE001 - one failed query must not kill the scene
            log_event({"scene_id": scene_id, "event": "DOWNLOADER_QUERY_FAILED", "query": query, "error": str(exc)})
    return queries


# --- Milestone/event log (full_novel_progress.jsonl) --------------------------
# Distinct from the Coordinator's coordinator_log.jsonl (envelopes/responses):
# this is the human-readable per-scene progress stream, and tests patch it.
_run_id_for_log = yaml.safe_load(RUN_CONFIG_PATH.read_text(encoding="utf-8"))["run_id"]
LOG_PATH = REPO_ROOT / f"shared/runs/{_run_id_for_log}/full_novel_progress.jsonl"


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


# ============================================================================
# Coordinator CODE core
# ============================================================================
class Coordinator:
    """Hub-and-spoke orchestration primitives (CLAUDE.md section 5).

    Every stage invocation goes through `invoke()` (or the manual
    `begin()`/`accept()` pair for the 09 HITL loop), which: builds and validates
    a Task Envelope, calls the stage's main(), validates the returned Stage
    Response against stage_response.schema.json AND (when the stage produced its
    primary artifact) that artifact against its expected_output_schema, and logs
    both envelope and response to shared/runs/<run_id>/coordinator_log.jsonl.
    """

    def __init__(self, run_config: dict) -> None:
        self.run_config = run_config
        self.run_id = run_config["run_id"]
        self.log_path = REPO_ROOT / f"shared/runs/{self.run_id}/coordinator_log.jsonl"
        self.run_config_ref = RUN_CONFIG_PATH.relative_to(REPO_ROOT).as_posix()

    def log(self, event: dict) -> None:
        event = {"ts": datetime.now(timezone.utc).isoformat(), **event}
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")

    def begin(self, stage: str, input_manifest: list[str], attempt: int = 1) -> TaskEnvelope:
        schema, _ = STAGE_CONTRACTS.get(stage, (_NO_PAYLOAD_SCHEMA, None))
        env = TaskEnvelope(
            envelope_id=str(uuid4()),
            run_id=self.run_id,
            stage=stage,
            attempt=attempt,
            input_manifest=list(input_manifest) or ["inputs"],
            run_config_ref=self.run_config_ref,
            expected_output_schema=schema,
        )
        validate_task_envelope(env)
        self.log({"event": "ENVELOPE", **env.to_dict()})
        return env

    def accept(self, env: TaskEnvelope, resp: StageResponse, output_dir: Path) -> StageResponse:
        """Validate + log a Stage Response produced for `env`. Validates the
        response wrapper always; validates the primary output payload against the
        envelope's expected_output_schema when the stage completed (or fell back)
        and that artifact exists on disk."""
        resp.envelope_id = env.envelope_id
        validate_stage_response(resp)
        schema, out_file = STAGE_CONTRACTS.get(env.stage, (_NO_PAYLOAD_SCHEMA, None))
        if out_file and resp.status in _PAYLOAD_OK_STATUSES:
            payload_path = output_dir / out_file
            if payload_path.exists():
                validate_against_schema(json.loads(payload_path.read_text(encoding="utf-8")), schema)
        self.log({"event": "RESPONSE", "stage": env.stage, **resp.to_dict()})
        return resp

    def invoke(
        self,
        stage: str,
        main_callable: Callable,
        input_dir: Path,
        output_dir: Path,
        *,
        input_manifest: list[str] | None = None,
        **kwargs,
    ) -> StageResponse:
        env = self.begin(stage, input_manifest or ["inputs"])
        resp = main_callable(input_dir, output_dir, self.run_config, **kwargs)
        return self.accept(env, resp, output_dir)

    def require_approval(self, stage_output_dir: Path, stage_label: str) -> bool:
        """Gate check (CLAUDE.md section 5): the Coordinator refuses to invoke the
        next stage without APPROVED.md on the human-review gate."""
        approved = (stage_output_dir / "APPROVED.md").exists()
        self.log({"event": "GATE_CHECK", "stage": stage_label, "approved": approved})
        return approved

    def approve(self, stage_output_dir: Path, note: str) -> None:
        stage_output_dir.mkdir(parents=True, exist_ok=True)
        (stage_output_dir / "APPROVED.md").write_text(note, encoding="utf-8")


# ============================================================================
# Music stage (09) HITL resolution
# ============================================================================
def _load_env_value(key: str) -> str | None:
    env_path = REPO_ROOT / "config" / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.partition("=")[2].strip() or None
    return None


def _build_music_source():
    """Jamendo (real CC music, requires JAMENDO_CLIENT_ID in config/.env) when
    available; the generated sine-bed placeholder otherwise. Returns
    (music_source, downloader_or_None) - Jamendo tracks download over plain HTTP
    so Stage 09's default downloader works; the generated source needs its
    synthesizing pseudo-downloader."""
    client_id = _load_env_value("JAMENDO_CLIENT_ID")
    if client_id:
        audio_spec = yaml.safe_load((REPO_ROOT / "config" / "audio_spec.yaml").read_text(encoding="utf-8"))
        return JamendoMusicSource(client_id=client_id, tag_map=audio_spec.get("jamendo_tag_map") or {}), None
    return GeneratedMusicSource(), generated_audio_downloader


def _single_scene_wide_cue_agent_call(beats_path: Path):
    """Deterministic fallback cue-sheet, bypassing the LLM entirely - not a
    guess, this exact remedy (one hand-authored scene-wide cue) is already a
    human-approved precedent in DECISIONS_LOG.md (2026-07-18) for this same real
    failure mode: llama3.2:3b repeatedly producing structurally invalid
    multi-cue boundaries. Used only after normal retries are exhausted."""
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


_STALE_TRACK_REF_RE = re.compile(r"not among cue '([^']+)'")


class _CachingMusicSource:
    """Wraps a real MusicSource so repeated `.search()` calls with the same
    mood_tags return the identical result within one resolve_music_stage()
    resolution sequence. A live network source (Jamendo) is not guaranteed to
    return byte-identical top-N results across two separate real calls - combined
    with the cue-sheet freeze below, this removes both sources of the
    non-determinism that produced a real "hitl_decisions references track_ref ...
    not among cue's candidates" crash (2026-07-23)."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self._cache: dict[tuple, list] = {}

    def search(self, mood_tags, max_results: int = 3):
        key = (tuple(mood_tags), max_results)
        if key not in self._cache:
            self._cache[key] = self._inner.search(mood_tags, max_results)
        return self._cache[key]


def _frozen_cue_sheet_agent_call(cues: list[dict]) -> Callable:
    """Replays an already-produced, already-schema-valid cue-sheet verbatim
    instead of calling the LLM again - used once a real attempt's cue-sheet
    clears validation, so later calls in the same resolution sequence can't have
    their cue_ids/mood_tags shift out from under an in-progress track choice."""
    def _call(system_prompt: str, user_message: str) -> str:  # noqa: ARG001
        return json.dumps({"cues": cues})
    return _call


def resolve_music_stage(run_config: dict, out_dir: Path, decisions_log: list) -> "StageResponse":
    """Stage 09's cue-sheet is a fresh (non-deterministic) LLM call on every
    invocation, and its live MusicSource search is re-run fresh too. Observed for
    real 2026-07-23: a track_ref recorded as this run's decision for cue_id X on
    attempt 1 was no longer among cue X's candidates on attempt 2, because BOTH
    the cue-sheet and the live Jamendo search had changed under it - Stage 09
    correctly refused to guess and returned FAILED.

    Root-cause fix: once an attempt's cue-sheet first clears validation
    (music_cue_intent.json exists), freeze it via `_frozen_cue_sheet_agent_call`
    for every later call in this same resolution sequence, and wrap music_source
    in `_CachingMusicSource`. The retry loop below also keeps a bounded
    drop-stale-and-retry safety net in case anything still slips through."""
    main09 = stage_main(9)
    music_source, music_downloader = _build_music_source()
    kwargs = dict(music_source=_CachingMusicSource(music_source))
    if music_downloader is not None:
        kwargs["downloader"] = music_downloader
    hitl_decisions: dict[str, str] = {}
    resp = main09(stage_dir(9) / "inputs", out_dir, run_config, **kwargs)
    intent_path = out_dir / "music_cue_intent.json"
    for _ in range(5):
        if "agent_call" not in kwargs and intent_path.exists():
            frozen_cues = json.loads(intent_path.read_text(encoding="utf-8")).get("cues") or []
            if frozen_cues:
                kwargs["agent_call"] = _frozen_cue_sheet_agent_call(frozen_cues)
        if resp.status == StageStatus.FAILED:
            stale_match = _STALE_TRACK_REF_RE.search(resp.error.message or "") if resp.error else None
            stale_cue_id = stale_match.group(1) if stale_match else None
            if stale_cue_id is not None and hitl_decisions.pop(stale_cue_id, None) is not None:
                decisions_log.append(
                    {
                        "stage": "09_audio_production",
                        "decision_point": "stale_track_selection",
                        "cue_id": stale_cue_id,
                        "policy": "dropped and re-asked: the previously chosen track_ref fell out of this cue's "
                        "candidates despite the cue-sheet freeze/music-search cache (belt-and-suspenders retry)",
                    }
                )
                resp = main09(
                    stage_dir(9) / "inputs", out_dir, run_config,
                    hitl_decisions=dict(hitl_decisions), selected_by="claude_autonomous_policy", **kwargs,
                )
                continue
            break
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

    persistent_stale_ref = resp.status == StageStatus.FAILED and _STALE_TRACK_REF_RE.search(
        resp.error.message or "" if resp.error else ""
    )
    if persistent_stale_ref or (
        resp.status == StageStatus.NEEDS_INPUT and any(item.reason_code == "cues_incomplete" for item in resp.needs_input)
    ):
        decisions_log.append(
            {
                "stage": "09_audio_production",
                "decision_point": "cues_incomplete" if not persistent_stale_ref else "stale_track_selection",
                "policy": "5 real attempts all failed structurally (invalid/incomplete beat ranges, or the live "
                "music search kept invalidating this run's prior track choice) - fell back to a deterministic "
                "single scene-wide cue (matches the human-approved 2026-07-18 precedent in DECISIONS_LOG.md for "
                "this same failure mode) instead of continuing to retry an unreliable LLM call/live search",
            }
        )
        beats_path = stage_dir(9) / "inputs" / "beats.json"
        fallback_kwargs = dict(kwargs, agent_call=_single_scene_wide_cue_agent_call(beats_path))
        resp = main09(stage_dir(9) / "inputs", out_dir, run_config, **fallback_kwargs)
        hitl_decisions = {}
        # Bounded retry here too: the deterministic single-cue sheet removes the
        # LLM's cue-boundary non-determinism, but the live music search behind it
        # is still re-run fresh on every call and can hit the same stale race.
        for _ in range(3):
            if resp.status == StageStatus.FAILED:
                stale_match = _STALE_TRACK_REF_RE.search(resp.error.message or "") if resp.error else None
                stale_cue_id = stale_match.group(1) if stale_match else None
                if stale_cue_id is not None and hitl_decisions.pop(stale_cue_id, None) is not None:
                    resp = main09(
                        stage_dir(9) / "inputs", out_dir, run_config,
                        hitl_decisions=dict(hitl_decisions), selected_by="claude_autonomous_policy", **fallback_kwargs,
                    )
                    continue
                break
            if resp.status != StageStatus.NEEDS_INPUT:
                break
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
                hitl_decisions=dict(hitl_decisions), selected_by="claude_autonomous_policy", **fallback_kwargs,
            )
    return resp


def clear_audio_cache(run_id: str) -> None:
    """Stage 09 caches narration by beat_id filename and cue tracks by cue_id
    filename - beat_id is scene-prefixed while cue_id is NOT, and this
    orchestrator re-runs Stage 09 for the same scene multiple times and processes
    multiple scenes under one shared run_id. Without this, a stale cue001.mp3
    from one scene gets silently reused for an unrelated cue. Cleared once per
    scene, before that scene's own Stage 09 call(s)."""
    run_cache = REPO_ROOT / f"shared/runs/{run_id}/cache"
    for sub in ("narration", "music"):
        d = run_cache / sub
        if d.exists():
            shutil.rmtree(d)


# ============================================================================
# Per-scene orchestration
# ============================================================================
def run_frontend(
    coord: Coordinator,
    run_config: dict,
    source_scenes: list[dict],
    source_text_dir: Path,
    *,
    screenplay_agent_call: Callable | None = None,
    scene_agent_call: Callable | None = None,
) -> tuple[list[dict], Path]:
    """Opt-in front-end pre-pass (CLAUDE.md steps 2-3): per source scene, run
    02_1_screenplay (prose -> screenplay) then 02_2_scene_extraction (screenplay
    -> film scenes), aggregating every film scene into a single run-level
    scenes_manifest so the normal per-scene loop can iterate it. Writes the
    combined manifest + one .txt per film scene into
    shared/runs/<run_id>/frontend/scenes/. Returns (combined_scenes, scenes_dir).

    agent_call injections are for tests only; production leaves them None so the
    stages use the real Ollama backend."""
    run_id = run_config["run_id"]
    scenes_dir = REPO_ROOT / f"shared/runs/{run_id}/frontend/scenes"
    if scenes_dir.exists():
        shutil.rmtree(scenes_dir)
    scenes_dir.mkdir(parents=True)

    combined: list[dict] = []
    order = 0
    for scene in source_scenes:
        src_id = scene["scene_id"]
        src_txt = source_text_dir / f"{src_id}.txt"
        if not src_txt.exists():
            log_event({"scene_id": src_id, "event": "FRONTEND_SKIP", "reason": "source text missing"})
            continue

        # 02_1 screenplay
        clean_frontend_io(SCREENPLAY_DIR)
        shutil.copy(src_txt, SCREENPLAY_DIR / "inputs" / f"{src_id}.txt")
        sp_kwargs = {"agent_call": screenplay_agent_call} if screenplay_agent_call else {}
        resp_sp = coord.invoke(
            "02_1_screenplay", _load_stage_main(SCREENPLAY_DIR, "_orch_stage_02_1"),
            SCREENPLAY_DIR / "inputs", SCREENPLAY_DIR / "outputs",
            input_manifest=[f"inputs/{src_id}.txt"], **sp_kwargs,
        )
        log_event({"scene_id": src_id, "stage": "02_1", "status": resp_sp.status.value, "summary": resp_sp.summary})
        screenplay_path = SCREENPLAY_DIR / "outputs" / "screenplay.json"
        if resp_sp.status != StageStatus.COMPLETE or not screenplay_path.exists():
            log_event({"scene_id": src_id, "event": "FRONTEND_HALT", "at": "02_1"})
            continue

        # 02_2 scene extraction
        clean_frontend_io(SCENE_EXTRACTION_DIR)
        shutil.copy(screenplay_path, SCENE_EXTRACTION_DIR / "inputs" / "screenplay.json")
        se_kwargs = {"agent_call": scene_agent_call} if scene_agent_call else {}
        resp_se = coord.invoke(
            "02_2_scene_extraction", _load_stage_main(SCENE_EXTRACTION_DIR, "_orch_stage_02_2"),
            SCENE_EXTRACTION_DIR / "inputs", SCENE_EXTRACTION_DIR / "outputs",
            input_manifest=["inputs/screenplay.json"], **se_kwargs,
        )
        log_event({"scene_id": src_id, "stage": "02_2", "status": resp_se.status.value, "summary": resp_se.summary})
        manifest_path = SCENE_EXTRACTION_DIR / "outputs" / "scenes_manifest.json"
        if resp_se.status != StageStatus.COMPLETE or not manifest_path.exists():
            log_event({"scene_id": src_id, "event": "FRONTEND_HALT", "at": "02_2"})
            continue

        se_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for film_scene in se_manifest.get("scenes", []):
            fs_id = film_scene["scene_id"]
            src_file = SCENE_EXTRACTION_DIR / "outputs" / f"{fs_id}.txt"
            if not src_file.exists():
                continue
            shutil.copy(src_file, scenes_dir / f"{fs_id}.txt")
            entry = dict(film_scene)
            entry["order"] = order
            entry["file_ref"] = f"frontend/scenes/{fs_id}.txt"
            combined.append(entry)
            order += 1

    combined_manifest = {
        "run_id": run_id,
        "manuscript_ref": run_config.get("manuscript_ref") or "frontend",
        "scenes": combined,
    }
    if combined:
        validate_against_schema(combined_manifest, "scenes_manifest.schema.json")
    (scenes_dir / "scenes_manifest.json").write_text(json.dumps(combined_manifest, indent=2), encoding="utf-8")
    coord.log({"event": "FRONTEND_DONE", "film_scene_count": len(combined)})
    return combined, scenes_dir


def clean_frontend_io(stage_path: Path) -> None:
    for sub in ("inputs", "outputs"):
        p = stage_path / sub
        if p.exists():
            shutil.rmtree(p)
        p.mkdir(parents=True)


def run_scene(
    coord: Coordinator,
    scene: dict,
    run_config: dict,
    reuse_beats: Path | None = None,
    scene_text_dir: Path | None = None,
) -> dict:
    scene_id = scene["scene_id"]
    scene_text_dir = scene_text_dir or MANUSCRIPT_OUTPUTS_DIR
    scene_txt_src = scene_text_dir / f"{scene_id}.txt"
    decisions: list = []
    result = {"scene_id": scene_id, "decisions": decisions, "stages": {}}

    def record(n, resp) -> None:
        result["stages"][n] = {
            "status": resp.status.value if hasattr(resp.status, "value") else resp.status,
            "summary": resp.summary,
            "needs_input": [i.to_dict() for i in resp.needs_input],
            "fallback_routed": [i.to_dict() for i in resp.fallback_routed],
            "error": resp.error.to_dict() if resp.error else None,
        }
        log_event({"scene_id": scene_id, "stage": n, **result["stages"][n]})

    # --- Stage 02: beat/shot division ---
    if reuse_beats and reuse_beats.exists():
        beats_path = reuse_beats
        log_event({"scene_id": scene_id, "stage": 2, "status": "REUSED", "summary": f"Reused existing {reuse_beats}"})
    else:
        clean_io(2)
        shutil.copy(scene_txt_src, stage_dir(2) / "inputs" / f"{scene_id}.txt")
        resp02 = coord.invoke(
            "02_beat_extraction", stage_main(2), stage_dir(2) / "inputs", stage_dir(2) / "outputs",
            input_manifest=[f"inputs/{scene_id}.txt"],
        )
        record(2, resp02)
        if resp02.status != StageStatus.COMPLETE:
            result["halted_at"] = 2
            return result
        beats_path = stage_dir(2) / "outputs" / "beats.json"

    # --- Downloader lane: populate the shared outputs/ pool then catalog it ---
    beats_data = json.loads(beats_path.read_text(encoding="utf-8"))
    scene_beats = beats_data.get("beats", [])

    download_scene_queries(scene_beats, scene_id)
    downloader_manifest = build_downloader_manifest(DOWNLOADER_OUTPUTS_DIR)
    log_event({"scene_id": scene_id, "event": "DOWNLOADER_CLIPS", "clip_count": downloader_manifest.get("clip_count", 0)})
    if not downloader_manifest.get("clips"):
        result["halted_at"] = "01_1 (no clips in the downloader outputs pool)"
        return result

    # --- Stage 01_2 scene_scoring (CLIP-rank the pool's clips per beat) ---
    ss_inputs, ss_outputs = SCENE_SCORING_DIR / "inputs", SCENE_SCORING_DIR / "outputs"
    for d in (ss_inputs, ss_outputs):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    shutil.copy(beats_path, ss_inputs / "beats.json")
    (ss_inputs / "downloader_manifest.json").write_text(json.dumps(downloader_manifest, indent=2), encoding="utf-8")
    resp_ss = coord.invoke(
        "01_2_scene_scoring", _load_stage_main(SCENE_SCORING_DIR, "_orch_stage_01_2"), ss_inputs, ss_outputs,
        input_manifest=["inputs/beats.json", "inputs/downloader_manifest.json"],
    )
    record("01_2", resp_ss)
    scene_scores_path = ss_outputs / "scene_scores.json"
    if resp_ss.status != StageStatus.COMPLETE or not scene_scores_path.exists():
        result["halted_at"] = "01_2"
        return result

    # --- Stage 09 audio (BEFORE shot mapping + timeline: narration length is
    # authoritative, and 09 reads only beats + scene text, no shot plan). ---
    clean_io(9)
    clear_audio_cache(run_config["run_id"])
    shutil.copy(beats_path, stage_dir(9) / "inputs" / "beats.json")
    shutil.copy(scene_txt_src, stage_dir(9) / "inputs" / "scene_text.txt")
    env09 = coord.begin("09_audio_production", ["inputs/beats.json", "inputs/scene_text.txt"])
    resp09 = resolve_music_stage(run_config, stage_dir(9) / "outputs", decisions)
    coord.accept(env09, resp09, stage_dir(9) / "outputs")
    record(9, resp09)
    audio_mix_path = stage_dir(9) / "outputs" / "audio_mix.json"
    scene_mix_path = stage_dir(9) / "outputs" / "scene_mix.wav"
    music_cue_intent_path = stage_dir(9) / "outputs" / "music_cue_intent.json"

    # --- Stage 07_2 narration_shot_mapping: physically extract short shots from
    # each beat's top-ranked downloader clips, covering its narration duration. ---
    sm_inputs, sm_outputs = SHOT_MAPPING_DIR / "inputs", SHOT_MAPPING_DIR / "outputs"
    for d in (sm_inputs, sm_outputs):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    shutil.copy(beats_path, sm_inputs / "beats.json")
    shutil.copy(scene_scores_path, sm_inputs / "scene_scores.json")
    (sm_inputs / "downloader_manifest.json").write_text(json.dumps(downloader_manifest, indent=2), encoding="utf-8")
    if audio_mix_path.exists():
        shutil.copy(audio_mix_path, sm_inputs / "audio_mix.json")
    resp_sm = coord.invoke(
        "07_2_narration_shot_mapping", _load_stage_main(SHOT_MAPPING_DIR, "_orch_stage_07_2"), sm_inputs, sm_outputs,
        input_manifest=["inputs/beats.json", "inputs/scene_scores.json", "inputs/downloader_manifest.json"],
    )
    record("07_2", resp_sm)
    edit_plan_path = sm_outputs / "edit_plan.json"
    assets_manifest_path = sm_outputs / "assets_manifest.json"
    shot_map_path = sm_outputs / "shot_map.json"
    if resp_sm.status not in (StageStatus.COMPLETE, StageStatus.FALLBACK_ROUTED) or not edit_plan_path.exists():
        result["halted_at"] = "07_2"
        return result
    merged_assets = json.loads(assets_manifest_path.read_text(encoding="utf-8"))

    # --- Stage 08 timeline. 07_2 already mapped shots onto narration, so
    # audio_mix is deliberately NOT passed - 08 lays the pre-extracted shots out
    # as-is (hard cuts, video length == narration length) rather than re-tiling. ---
    clean_io(8)
    shutil.copy(edit_plan_path, stage_dir(8) / "inputs" / "edit_plan.json")
    shutil.copy(assets_manifest_path, stage_dir(8) / "inputs" / "assets_manifest.json")
    resp08 = coord.invoke(
        "08_timeline_builder", stage_main(8), stage_dir(8) / "inputs", stage_dir(8) / "outputs",
        input_manifest=["inputs/edit_plan.json", "inputs/assets_manifest.json"],
    )
    record(8, resp08)
    if resp08.status not in (StageStatus.COMPLETE, StageStatus.FALLBACK_ROUTED):
        result["halted_at"] = 8
        return result
    timeline_path = stage_dir(8) / "outputs" / "timeline.json"
    if not timeline_path.exists():
        result["halted_at"] = 8
        return result

    # --- Stage 10 human review gate ---
    clean_io(10)
    shutil.copy(timeline_path, stage_dir(10) / "inputs" / "timeline.json")
    if audio_mix_path.exists():
        shutil.copy(audio_mix_path, stage_dir(10) / "inputs" / "audio_mix.json")
    resp10 = coord.invoke(
        "10_human_review_gate", stage_main(10), stage_dir(10) / "inputs", stage_dir(10) / "outputs",
        input_manifest=["inputs/timeline.json"],
    )
    record(10, resp10)
    coord.approve(
        stage_dir(10) / "outputs",
        f"# Stage 10 - human_review_gate - APPROVED\n\n"
        f"Auto-approved under the human-authorized 'autonomous with summary review' policy "
        f"for this full-novel bulk run (scene {scene_id}). Every judgment call this policy made "
        f"for this scene is itemized in this run's consolidated summary, not individually approved here.\n\n"
        f"**Timestamp:** {datetime.now(timezone.utc).isoformat()}\n",
    )

    # --- Stage 11 assembly (gated on Stage 10 approval, CLAUDE.md section 5) ---
    if not coord.require_approval(stage_dir(10) / "outputs", "10_human_review_gate"):
        result["halted_at"] = "10 (not approved)"
        return result
    clean_io(11)
    shutil.copy(timeline_path, stage_dir(11) / "inputs" / "timeline.json")
    if scene_mix_path.exists():
        shutil.copy(scene_mix_path, stage_dir(11) / "inputs" / "scene_mix.wav")
    resp11 = coord.invoke(
        "11_assembly_render", stage_main(11), stage_dir(11) / "inputs", stage_dir(11) / "outputs",
        input_manifest=["inputs/timeline.json"],
    )
    record(11, resp11)
    final_mp4_path = stage_dir(11) / "outputs" / "final.mp4"
    if resp11.status != StageStatus.COMPLETE or not final_mp4_path.exists():
        result["halted_at"] = 11
        return result

    # --- Stage 12 qa ---
    clean_io(12)
    for name, path in [
        ("beats.json", beats_path),
        ("scene_scores.json", scene_scores_path),
        ("edit_plan.json", edit_plan_path),
        ("timeline.json", timeline_path),
        ("final.mp4", final_mp4_path),
    ]:
        shutil.copy(path, stage_dir(12) / "inputs" / name)
    (stage_dir(12) / "inputs" / "assets_manifest.json").write_text(json.dumps(merged_assets, indent=2), encoding="utf-8")
    if shot_map_path.exists():
        shutil.copy(shot_map_path, stage_dir(12) / "inputs" / "shot_map.json")
    if music_cue_intent_path.exists():
        shutil.copy(music_cue_intent_path, stage_dir(12) / "inputs" / "music_cue_intent.json")
    if audio_mix_path.exists():
        shutil.copy(audio_mix_path, stage_dir(12) / "inputs" / "audio_mix.json")
    manifest_path = REPO_ROOT / f"shared/runs/{run_config['run_id']}/manifest.json"
    if manifest_path.exists():
        shutil.copy(manifest_path, stage_dir(12) / "inputs" / "manifest.json")
    resp12 = coord.invoke(
        "12_qa_attribution", stage_main(12), stage_dir(12) / "inputs", stage_dir(12) / "outputs",
        input_manifest=["inputs/timeline.json", "inputs/assets_manifest.json"],
    )
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

    # --- Stage 13 pixel art ---
    clean_io(13)
    shutil.copy(final_mp4_path, stage_dir(13) / "inputs" / "final.mp4")
    resp13 = coord.invoke(
        "13_pixel_art_conversion", stage_main(13), stage_dir(13) / "inputs", stage_dir(13) / "outputs",
        input_manifest=["inputs/final.mp4"],
    )
    record(13, resp13)
    final_pixel_path = stage_dir(13) / "outputs" / "final_pixel_art.mp4"

    # --- Stage 14 anime style (the author's chosen primary deliverable).
    # SKIP_STAGE_14 env var: an ad-hoc, per-run opt-out - the CPU-only GAN pass
    # was calibrated against much shorter test chapters; a real full-chapter run
    # needs an estimated 4-5+ hours, and the author asked to skip it for some
    # runs. ---
    if os.environ.get("SKIP_STAGE_14", "").strip().lower() in ("1", "true", "yes"):
        log_event({"scene_id": scene_id, "stage": 14, "status": "SKIPPED", "summary": "Skipped via SKIP_STAGE_14 for this run."})
        # Stage 14 did NOT run this scene. Do not point at stage_dir(14)/outputs -
        # a prior run's leftover final_anime.mp4 (the skip branch doesn't clean
        # it) would otherwise be archived and reported as THIS run's final video,
        # a stale-artifact bug caught on a real run 2026-07-24.
        final_anime_path = None
    else:
        clean_io(14)
        shutil.copy(final_mp4_path, stage_dir(14) / "inputs" / "final.mp4")
        resp14 = coord.invoke(
            "14_anime_style_conversion", stage_main(14), stage_dir(14) / "inputs", stage_dir(14) / "outputs",
            input_manifest=["inputs/final.mp4"],
        )
        record(14, resp14)
        final_anime_path = stage_dir(14) / "outputs" / "final_anime.mp4"

    # --- Archive this scene's final artifacts before the next scene's clean_io() wipes them ---
    archive_dir = REPO_ROOT / f"shared/runs/{run_config['run_id']}/chapter_outputs/{scene_id}"
    archive_dir.mkdir(parents=True, exist_ok=True)
    # A stale final_anime.mp4 from a prior run must not linger in this run's
    # archive when Stage 14 was skipped this time.
    stale_anime = archive_dir / "final_anime.mp4"
    if final_anime_path is None and stale_anime.exists():
        stale_anime.unlink()
    shutil.copy(final_mp4_path, archive_dir / "final_raw.mp4")
    if final_pixel_path.exists():
        shutil.copy(final_pixel_path, archive_dir / "final_pixel_art.mp4")
    if final_anime_path is not None and final_anime_path.exists():
        shutil.copy(final_anime_path, archive_dir / "final_anime.mp4")
        result["final_video"] = str(archive_dir / "final_anime.mp4")
    else:
        result["final_video"] = str(archive_dir / "final_raw.mp4")
    result["completed"] = True
    return result


def main(scene_ids: list[str] | None = None) -> int:
    run_config = yaml.safe_load(RUN_CONFIG_PATH.read_text(encoding="utf-8"))
    coord = Coordinator(run_config)

    # Opt-in front-end (default off): regenerate the scene set from prose via
    # 02_1 screenplay + 02_2 scene extraction. Off = the existing pre-split
    # scenes_manifest drives the run exactly as before.
    scene_text_dir: Path | None = None
    if _frontend_enabled(run_config):
        source_scenes = load_scenes()
        log_event({"event": "FRONTEND_START", "source_scene_count": len(source_scenes)})
        scenes, scene_text_dir = run_frontend(coord, run_config, source_scenes, MANUSCRIPT_OUTPUTS_DIR)
    else:
        scenes = load_scenes()
    if scene_ids:
        scenes = [s for s in scenes if s["scene_id"] in scene_ids]

    # ch1_sc1 beat reuse only applies to the default (marker-split) scene set -
    # the front-end regenerates scene ids, so its scenes never match this reuse.
    existing_ch1_beats = stage_dir(2) / "outputs" / "beats.json"
    ch1_beats_backup = REPO_ROOT / f"shared/runs/{run_config['run_id']}/ch1_sc1_beats_backup.json"
    if scene_text_dir is None and existing_ch1_beats.exists() and not ch1_beats_backup.exists():
        ch1_beats_backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(existing_ch1_beats, ch1_beats_backup)

    all_results = []
    for scene in scenes:
        reuse = (
            ch1_beats_backup
            if scene_text_dir is None and scene["scene_id"] == "ch1_sc1" and ch1_beats_backup.exists()
            else None
        )
        log_event({"scene_id": scene["scene_id"], "event": "SCENE_START"})
        coord.log({"event": "SCENE_START", "scene_id": scene["scene_id"]})
        try:
            res = run_scene(coord, scene, run_config, reuse_beats=reuse, scene_text_dir=scene_text_dir)
        except Exception as exc:  # noqa: BLE001 - a bulk run must not die on one scene's crash
            res = {"scene_id": scene["scene_id"], "crashed": True, "error": str(exc), "traceback": traceback.format_exc()}
            log_event({"scene_id": scene["scene_id"], "event": "SCENE_CRASHED", "error": str(exc)})
        all_results.append(res)
        log_event({"scene_id": scene["scene_id"], "event": "SCENE_DONE", "completed": res.get("completed", False)})

    summary_path = REPO_ROOT / f"shared/runs/{run_config['run_id']}/full_novel_summary.json"
    summary_path.write_text(json.dumps(all_results, indent=2), encoding="utf-8")
    log_event({"event": "RUN_DONE", "summary_path": str(summary_path)})
    coord.log({"event": "RUN_DONE", "summary_path": str(summary_path)})
    return 0


if __name__ == "__main__":
    scene_arg = sys.argv[1:] if len(sys.argv) > 1 else None
    sys.exit(main(scene_arg))
