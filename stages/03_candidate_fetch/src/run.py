"""Stage 03: candidate_fetch.

For each beat, queries every configured FootageSource with search terms
derived mechanically from the beat's visual_description (no agent involved -
CLAUDE.md classifies this stage CODE). Captures license/attribution into the
run manifest at fetch time (CLAUDE.md rule 12) and writes candidates.json.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from shared.envelopes import ErrorInfo, NeedsInputItem, StageResponse, StageStatus, validate_against_schema  # noqa: E402
from shared.manifest import append_manifest_entries  # noqa: E402
from shared.sources import FootageSource, PexelsSource, PixabaySource  # noqa: E402

STAGE_NAME = "03_candidate_fetch"

_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "of", "in", "on", "at", "to", "from",
    "with", "her", "his", "she", "he", "it", "its", "their", "they", "is",
    "are", "was", "were", "as", "into", "onto", "toward", "towards", "behind",
    "beside", "under", "over", "through", "across", "down", "up", "out",
    "for", "that", "this", "these", "those", "who", "which", "one", "only",
    "them", "there", "than", "then", "so", "if", "be", "been", "being",
}


def extract_search_terms(visual_description: str, max_terms: int = 8) -> str:
    """Mechanical (non-agent) keyword extraction: lowercase, drop stopwords and
    short words, de-duplicate, keep the first max_terms in reading order."""
    words = re.findall(r"[A-Za-z']+", visual_description.lower())
    seen: set[str] = set()
    terms: list[str] = []
    for w in words:
        if w in _STOPWORDS or len(w) <= 2 or w in seen:
            continue
        seen.add(w)
        terms.append(w)
        if len(terms) >= max_terms:
            break
    return " ".join(terms)


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if value:
            env[key.strip()] = value
    return env


def _default_sources() -> dict[str, FootageSource]:
    env = {**_load_env_file(REPO_ROOT / "config" / ".env"), **os.environ}
    sources: dict[str, FootageSource] = {}
    if env.get("PEXELS_API_KEY"):
        sources["pexels"] = PexelsSource(api_key=env["PEXELS_API_KEY"])
    if env.get("PIXABAY_API_KEY"):
        sources["pixabay"] = PixabaySource(api_key=env["PIXABAY_API_KEY"])
    return sources


def _default_max_results() -> int:
    thresholds = yaml.safe_load((REPO_ROOT / "config" / "thresholds.yaml").read_text(encoding="utf-8"))
    return thresholds["candidate_fetch"]["max_results_per_source"]


def _candidate_to_dict(c) -> dict:
    d = {
        "candidate_id": c.candidate_id,
        "source": c.source,
        "url": c.url,
        "license": c.license,
        "thumbnail_ref": c.thumbnail_ref,
    }
    if c.download_url:
        d["download_url"] = c.download_url
    if c.duration_s is not None:
        d["duration_s"] = c.duration_s
    if c.creator:
        d["creator"] = c.creator
    return d


def main(
    input_dir: Path,
    output_dir: Path,
    run_config: dict,
    sources: dict[str, FootageSource] | None = None,
    max_results: int | None = None,
) -> StageResponse:
    run_id = run_config["run_id"]
    beats_path = input_dir / "beats.json"

    if not beats_path.exists():
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(message=f"beats.json not found at {beats_path}"),
        )

    beats_data = json.loads(beats_path.read_text(encoding="utf-8"))
    beats = beats_data.get("beats", [])
    if not beats:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(message=f"{beats_path} has zero beats - nothing to search for."),
        )

    sources = sources if sources is not None else _default_sources()
    if not sources:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.NEEDS_INPUT,
            needs_input=[
                NeedsInputItem(
                    reason_code="no_sources_configured",
                    question=(
                        "No FootageSource has a usable API key (checked config/.env and "
                        "the environment for PEXELS_API_KEY / PIXABAY_API_KEY). Add a key "
                        "to config/.env and re-run."
                    ),
                    options=["Add PEXELS_API_KEY", "Add PIXABAY_API_KEY", "Add both"],
                )
            ],
        )

    max_results = max_results if max_results is not None else _default_max_results()

    cache: dict[tuple[str, str], list] = {}
    candidates_by_beat = []
    manifest_entries: list[dict] = []
    call_failures = 0
    now = datetime.now(timezone.utc).isoformat()

    for beat in beats:
        beat_id = beat["beat_id"]
        query = extract_search_terms(beat["visual_description"])
        beat_candidates = []
        for source_name, source in sources.items():
            cache_key = (source_name, query)
            if cache_key in cache:
                results = cache[cache_key]
            else:
                try:
                    results = source.search(query, max_results)
                except requests.RequestException:
                    results = []
                    call_failures += 1
                cache[cache_key] = results

            for c in results:
                beat_candidates.append(_candidate_to_dict(c))
                entry = {
                    "entry_id": c.candidate_id,
                    "kind": "footage",
                    "fetched_by_stage": STAGE_NAME,
                    "fetched_at": now,
                    "source": c.source,
                    "license": c.license,
                    "attribution_required": c.requires_attribution,
                }
                if c.url:
                    entry["source_url"] = c.url
                if c.creator:
                    entry["creator"] = c.creator
                manifest_entries.append(entry)

        candidates_by_beat.append(
            {"beat_id": beat_id, "search_terms": [query], "candidates": beat_candidates}
        )

    output = {
        "run_id": run_id,
        "scene_id": beats_data.get("scene_id", ""),
        "candidates_by_beat": candidates_by_beat,
    }
    validate_against_schema(output, "candidates.schema.json")

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "candidates.json").write_text(json.dumps(output, indent=2), encoding="utf-8")

    # De-dupe manifest entries by entry_id (the same candidate can surface for
    # multiple beats if their search terms collide) before appending.
    seen_ids: set[str] = set()
    deduped_entries = []
    for entry in manifest_entries:
        if entry["entry_id"] in seen_ids:
            continue
        seen_ids.add(entry["entry_id"])
        deduped_entries.append(entry)
    append_manifest_entries(REPO_ROOT / "shared" / "runs" / run_id, run_id, deduped_entries)

    total_candidates = sum(len(b["candidates"]) for b in candidates_by_beat)
    zero_candidate_beats = [b["beat_id"] for b in candidates_by_beat if not b["candidates"]]

    summary = (
        f"Fetched {total_candidates} candidate(s) for {len(beats)} beat(s) "
        f"across {len(sources)} source(s) ({', '.join(sorted(sources))})."
    )
    if zero_candidate_beats:
        summary += f" {len(zero_candidate_beats)} beat(s) with zero candidates (will route to fallback): {zero_candidate_beats}."
    if call_failures:
        summary += f" {call_failures} API call(s) failed and were skipped."

    return StageResponse(
        envelope_id="",
        run_id=run_id,
        stage=STAGE_NAME,
        status=StageStatus.COMPLETE,
        summary=summary,
        output_manifest=["outputs/candidates.json"],
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
