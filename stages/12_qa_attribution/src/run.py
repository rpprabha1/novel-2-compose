"""Stage 12: qa_attribution.

Validates every run artifact against its schema, checks the run manifest's
attribution completeness, and checks final.mp4's duration/loudness against
configured targets. Emits qa_report.json + CREDITS.md. CODE - fully
deterministic, no agent involvement, no creative judgment (CLAUDE.md).

A failing qa_report.pass blocks the run from being marked done but never
blocks re-running an upstream stage to fix the underlying issue.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from shared.envelopes import ErrorInfo, StageResponse, StageStatus, validate_against_schema  # noqa: E402
from shared.media import FFmpegError, probe_duration_s  # noqa: E402

STAGE_NAME = "12_qa_attribution"

ARTIFACT_SCHEMA_MAP = {
    "beats.json": "beats.schema.json",
    "candidates.json": "candidates.schema.json",
    "scene_scores.json": "scene_scores.schema.json",
    "shot_map.json": "shot_map.schema.json",
    "assets_manifest.json": "assets_manifest.schema.json",
    "edit_plan.json": "edit_plan.schema.json",
    "timeline.json": "timeline.schema.json",
    "music_cue_intent.json": "music_cue_intent.schema.json",
    "audio_mix.json": "audio_mix.schema.json",
    "manifest.json": "manifest.schema.json",
}

# Artifacts that a run may legitimately not have, validated only if present.
# `candidates.json` (the stock Pexels/Pixabay lane's output) and
# `scene_scores.json` (the downloader lane's output) are mutually exclusive
# footage-lane artifacts as of the 2026-07-23 downloader-lane cutover - a run
# uses one lane or the other, so neither is required, but whichever is present
# is still schema-checked.
_OPTIONAL_ARTIFACTS = {"candidates.json", "scene_scores.json", "shot_map.json"}

_KIND_TITLES = {"footage": "Footage", "music": "Music", "generated_image": "Generated Assets"}


def _check_schema_validation(input_dir: Path) -> tuple[dict, dict]:
    errors: list[str] = []
    loaded: dict[str, dict] = {}
    for filename, schema_name in ARTIFACT_SCHEMA_MAP.items():
        path = input_dir / filename
        if not path.exists():
            if filename not in _OPTIONAL_ARTIFACTS:
                errors.append(f"{filename}: missing")
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{filename}: invalid JSON ({exc})")
            continue
        try:
            validate_against_schema(data, schema_name)
        except jsonschema.ValidationError as exc:
            errors.append(f"{filename}: {exc.message}")
            continue
        loaded[filename] = data
    check = {
        "name": "schema_validation",
        "pass": not errors,
        "detail": "; ".join(errors) if errors else f"All {len(loaded)} present artifact(s) schema-valid",
    }
    return check, loaded


def _check_attribution_completeness(manifest: dict | None) -> dict:
    if manifest is None:
        return {"name": "attribution_completeness", "pass": False, "detail": "manifest.json not available - cannot check attribution"}
    errors = []
    for entry in manifest.get("entries", []):
        if not entry.get("license"):
            errors.append(f"{entry.get('entry_id')}: missing license")
        if entry.get("attribution_required") and not entry.get("creator"):
            errors.append(f"{entry.get('entry_id')}: attribution required but no creator recorded")
    n = len(manifest.get("entries", []))
    return {
        "name": "attribution_completeness",
        "pass": not errors,
        "detail": "; ".join(errors) if errors else f"All {n} manifest entries have complete attribution",
    }


def _check_duration_tolerance(final_mp4_path: Path, audio_mix: dict | None, tolerance_pct: float) -> dict:
    if audio_mix is None:
        return {"name": "duration_tolerance", "pass": False, "detail": "audio_mix.json not available - cannot check duration"}
    target = audio_mix.get("total_duration_s")
    if not target:
        return {"name": "duration_tolerance", "pass": False, "detail": "audio_mix.json missing total_duration_s"}
    try:
        actual = probe_duration_s(final_mp4_path)
    except FFmpegError as exc:
        return {"name": "duration_tolerance", "pass": False, "detail": f"could not probe final.mp4: {exc}"}
    drift_pct = abs(actual - target) / target * 100
    detail = f"final.mp4={actual:.3f}s vs target={target:.3f}s (drift {drift_pct:.3f}%, limit {tolerance_pct}%)"
    return {"name": "duration_tolerance", "pass": drift_pct <= tolerance_pct, "detail": detail}


def _check_loudness_spec(audio_mix: dict | None, target_lufs: float, tolerance_lu: float) -> dict:
    if audio_mix is None:
        return {"name": "loudness_spec", "pass": False, "detail": "audio_mix.json not available - cannot check loudness"}
    final_lufs = audio_mix.get("final_lufs")
    if final_lufs is None:
        return {"name": "loudness_spec", "pass": False, "detail": "audio_mix.json missing final_lufs"}
    diff = abs(final_lufs - target_lufs)
    detail = f"final_lufs={final_lufs} vs target={target_lufs} (diff {diff:.3f}, tolerance {tolerance_lu})"
    return {"name": "loudness_spec", "pass": diff <= tolerance_lu, "detail": detail}


def _build_credits_md(manifest: dict | None, run_id: str) -> str:
    if manifest is None:
        return f"# CREDITS.md\n\nrun_id: {run_id}\n\nNo manifest available - attribution could not be compiled.\n"
    by_kind: dict[str, list[dict]] = {}
    for entry in manifest.get("entries", []):
        by_kind.setdefault(entry.get("kind", "other"), []).append(entry)
    lines = ["# CREDITS.md", "", f"run_id: {run_id}", ""]
    for kind, title in _KIND_TITLES.items():
        entries = by_kind.get(kind, [])
        if not entries:
            continue
        lines.append(f"## {title}")
        lines.append("")
        for e in entries:
            creator = f" by {e['creator']}" if e.get("creator") else ""
            url = f" ([source]({e['source_url']}))" if e.get("source_url") else ""
            lines.append(f"- **{e['entry_id']}**{creator} — {e['source']}, {e['license']}{url}")
        lines.append("")
    return "\n".join(lines)


def main(input_dir: Path, output_dir: Path, run_config: dict, thresholds: dict | None = None, audio_spec: dict | None = None) -> StageResponse:
    run_id = run_config["run_id"]
    final_mp4_path = input_dir / "final.mp4"
    if not final_mp4_path.exists():
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(message=f"final.mp4 not found in {input_dir} - nothing to QA."),
        )

    thresholds = thresholds or yaml.safe_load((REPO_ROOT / "config" / "thresholds.yaml").read_text(encoding="utf-8"))
    audio_spec = audio_spec or yaml.safe_load((REPO_ROOT / "config" / "audio_spec.yaml").read_text(encoding="utf-8"))

    schema_check, loaded = _check_schema_validation(input_dir)
    manifest = loaded.get("manifest.json")
    audio_mix = loaded.get("audio_mix.json")

    checks = [
        schema_check,
        _check_attribution_completeness(manifest),
        _check_duration_tolerance(final_mp4_path, audio_mix, thresholds["qa"]["duration_tolerance_pct"]),
        _check_loudness_spec(audio_mix, audio_spec["loudness"]["target_lufs"], audio_spec["loudness"]["tolerance_lu"]),
    ]
    overall_pass = all(c["pass"] for c in checks)

    scene_id = ""
    for artifact in loaded.values():
        if artifact.get("scene_id"):
            scene_id = artifact["scene_id"]
            break

    qa_report = {"run_id": run_id, "scene_id": scene_id, "checks": checks, "pass": overall_pass}
    validate_against_schema(qa_report, "qa_report.schema.json")

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "qa_report.json").write_text(json.dumps(qa_report, indent=2), encoding="utf-8")
    (output_dir / "CREDITS.md").write_text(_build_credits_md(manifest, run_id), encoding="utf-8")

    output_manifest = ["outputs/qa_report.json", "outputs/CREDITS.md"]
    failed_names = [c["name"] for c in checks if not c["pass"]]

    if not overall_pass:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(message=f"QA failed: {failed_names}. See outputs/qa_report.json for details."),
            output_manifest=output_manifest,
        )

    return StageResponse(
        envelope_id="",
        run_id=run_id,
        stage=STAGE_NAME,
        status=StageStatus.COMPLETE,
        summary=f"QA passed: all {len(checks)} checks green. CREDITS.md written with {len(manifest.get('entries', [])) if manifest else 0} entries.",
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
