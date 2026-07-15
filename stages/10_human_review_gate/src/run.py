"""Stage 10: human_review_gate.

Generates a contact-sheet HTML (thumbnail grid + timeline scrub markers +
audio cue markers) from timeline.json and audio_mix.json for human review.
CODE + human - no agent involvement (CLAUDE.md section 2). The human
approves (writes outputs/APPROVED.md) or requests changes; this stage's only
job is rendering a reviewable artifact, never deciding anything itself.
"""

from __future__ import annotations

import base64
import json
import re
import sys
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Callable

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from shared.envelopes import ErrorInfo, StageResponse, StageStatus  # noqa: E402
from shared.media import FFmpegError, extract_thumbnail  # noqa: E402

STAGE_NAME = "10_human_review_gate"

ThumbnailExtractorFn = Callable[[Path, float, Path], None]

# Categorical slots 1/2/3 (blue/aqua/yellow) from the project's dataviz
# palette, fixed order by track identity (video/narration/music) - never
# reassigned based on which tracks happen to be present in a given run.
_COLOR_VIDEO = "#2a78d6"
_COLOR_NARRATION = "#1baf7a"
_COLOR_MUSIC = "#eda100"


def _default_thumbnail_extractor(video_path: Path, timestamp_s: float, dest_path: Path) -> None:
    extract_thumbnail(video_path, timestamp_s, dest_path)


def _safe_filename(value: str) -> str:
    """shot_id is displayed HTML-escaped already, but is also used as a cache
    filename component - sanitize separately so an unexpected character
    (or, defensively, a path-traversal attempt) can't affect the filesystem."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value) or "shot"


def _load_manifest_by_id(run_id: str) -> dict:
    manifest_path = REPO_ROOT / "shared" / "runs" / run_id / "manifest.json"
    if not manifest_path.exists():
        return {}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {e["entry_id"]: e for e in manifest.get("entries", [])}


def _fmt_t(seconds: float) -> str:
    m, s = divmod(seconds, 60)
    return f"{int(m)}:{s:04.1f}"


def _timeline_row(label: str, color: str, segments: list[dict], total_s: float) -> str:
    """segments: list of {start_s, duration_s, title} -> one CSS row of proportional blocks."""
    blocks = []
    for seg in segments:
        left_pct = 0 if total_s <= 0 else seg["start_s"] / total_s * 100
        width_pct = 0 if total_s <= 0 else seg["duration_s"] / total_s * 100
        blocks.append(
            f'<div class="seg" style="left:{left_pct:.3f}%;width:{width_pct:.3f}%;background:{color};" '
            f'title="{escape(seg["title"])}"></div>'
        )
    return (
        f'<div class="track-row"><div class="track-label">{escape(label)}</div>'
        f'<div class="track-bar">{"".join(blocks)}</div></div>'
    )


def _build_html(run_id: str, scene_id: str, timeline: dict, audio_mix: dict, thumbnails_b64: dict[str, str]) -> str:
    manifest_by_id = _load_manifest_by_id(run_id)
    clips = timeline.get("clips", [])
    total_s = timeline.get("total_duration_s", 0.0)

    video_segments = [
        {
            "start_s": c["timeline_start_s"],
            "duration_s": c["timeline_end_s"] - c["timeline_start_s"],
            "title": f"{c['shot_id']} ({_fmt_t(c['timeline_start_s'])}-{_fmt_t(c['timeline_end_s'])})",
        }
        for c in clips
    ]
    narration_segments = [
        {
            "start_s": s["start_s"],
            "duration_s": s["duration_s"],
            "title": f"{s['beat_id']} ({_fmt_t(s['start_s'])}-{_fmt_t(s['start_s'] + s['duration_s'])})",
        }
        for s in audio_mix.get("narration_stems", [])
    ]
    music_segments = [
        {
            "start_s": s["start_s"],
            "duration_s": s["duration_s"],
            "title": f"{s['track_ref']} ({_fmt_t(s['start_s'])}-{_fmt_t(s['start_s'] + s['duration_s'])})",
        }
        for s in audio_mix.get("music_stems", [])
    ]

    timeline_html = (
        '<div class="timeline">'
        + _timeline_row("Video", _COLOR_VIDEO, video_segments, total_s)
        + _timeline_row("Narration", _COLOR_NARRATION, narration_segments, total_s)
        + _timeline_row("Music", _COLOR_MUSIC, music_segments, total_s)
        + "</div>"
    )

    cards = []
    for c in clips:
        asset_id = Path(c["file_ref"]).stem
        entry = manifest_by_id.get(asset_id, {})
        transition = c.get("transition_out", {}).get("type", "(end of scene)")
        thumb_b64 = thumbnails_b64.get(c["shot_id"], "")
        license_cell = escape(entry.get("license", ""))
        if entry.get("creator"):
            license_cell += f" - {escape(entry['creator'])}"
        cards.append(
            f"""<div class="card">
  <img src="data:image/jpeg;base64,{thumb_b64}" alt="{escape(c['shot_id'])}" />
  <div class="card-body">
    <div class="card-title">{escape(c['shot_id'])}</div>
    <div class="card-meta">{_fmt_t(c['timeline_start_s'])} - {_fmt_t(c['timeline_end_s'])} ({c['timeline_end_s'] - c['timeline_start_s']:.2f}s)</div>
    <div class="card-meta">transition out: <strong>{escape(transition)}</strong></div>
    <div class="card-meta">{license_cell}</div>
  </div>
</div>"""
        )

    narration_rows = "".join(
        f"<tr><td>{escape(s['beat_id'])}</td><td>{_fmt_t(s['start_s'])}</td><td>{s['duration_s']:.2f}s</td></tr>"
        for s in audio_mix.get("narration_stems", [])
    )
    music_rows = []
    for s in audio_mix.get("music_stems", []):
        entry = manifest_by_id.get(s["track_ref"], {})
        license_cell = escape(entry.get("license", ""))
        if entry.get("creator"):
            license_cell += f" - {escape(entry['creator'])}"
        music_rows.append(
            f"<tr><td>{escape(s['cue_id'])}</td><td>{escape(s['track_ref'])}</td>"
            f"<td>{_fmt_t(s['start_s'])}</td><td>{s['duration_s']:.2f}s</td>"
            f"<td>{license_cell}</td>"
            f"<td>{escape(s.get('selected_by', ''))}</td></tr>"
        )

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Contact Sheet - {escape(run_id)} / {escape(scene_id)}</title>
<style>
  :root {{
    color-scheme: light;
    --surface-1: #fcfcfb; --surface-2: #f9f9f7;
    --text-primary: #0b0b0b; --text-secondary: #52514e; --text-muted: #898781;
    --border: rgba(11,11,11,0.10); --grid: #e1e0d9;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:where(:not([data-theme="light"])) {{
      color-scheme: dark;
      --surface-1: #1a1a19; --surface-2: #0d0d0d;
      --text-primary: #ffffff; --text-secondary: #c3c2b7; --text-muted: #898781;
      --border: rgba(255,255,255,0.10); --grid: #2c2c2a;
    }}
  }}
  :root[data-theme="dark"] {{
    color-scheme: dark;
    --surface-1: #1a1a19; --surface-2: #0d0d0d;
    --text-primary: #ffffff; --text-secondary: #c3c2b7; --text-muted: #898781;
    --border: rgba(255,255,255,0.10); --grid: #2c2c2a;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 32px; background: var(--surface-2); color: var(--text-primary);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .subtitle {{ color: var(--text-secondary); font-size: 13px; margin-bottom: 24px; }}
  section {{ margin-bottom: 32px; }}
  h2 {{ font-size: 14px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-secondary); margin: 0 0 12px; }}
  .timeline {{ background: var(--surface-1); border: 1px solid var(--border); border-radius: 8px; padding: 16px; overflow-x: auto; }}
  .track-row {{ display: flex; align-items: center; height: 32px; margin-bottom: 6px; }}
  .track-label {{ width: 90px; flex-shrink: 0; font-size: 12px; color: var(--text-secondary); }}
  .track-bar {{ position: relative; flex: 1; min-width: 480px; height: 20px; background: var(--grid); border-radius: 4px; }}
  .seg {{ position: absolute; top: 0; height: 100%; border-radius: 3px; border: 1px solid var(--surface-2); cursor: default; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 16px; }}
  .card {{ background: var(--surface-1); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }}
  .card img {{ width: 100%; aspect-ratio: 16/9; object-fit: cover; display: block; background: var(--grid); }}
  .card-body {{ padding: 10px 12px; }}
  .card-title {{ font-weight: 600; font-size: 13px; margin-bottom: 4px; }}
  .card-meta {{ font-size: 12px; color: var(--text-secondary); }}
  table {{ width: 100%; border-collapse: collapse; background: var(--surface-1); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; font-size: 13px; }}
  th, td {{ text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--grid); }}
  th {{ color: var(--text-secondary); font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: 0.03em; }}
  tr:last-child td {{ border-bottom: none; }}
</style>
</head>
<body>
  <h1>Contact Sheet</h1>
  <div class="subtitle">run_id: {escape(run_id)} &middot; scene_id: {escape(scene_id)} &middot; total duration: {_fmt_t(total_s)} &middot; generated {generated_at}</div>

  <section>
    <h2>Timeline</h2>
    {timeline_html}
  </section>

  <section>
    <h2>Shots ({len(clips)})</h2>
    <div class="grid">
      {"".join(cards)}
    </div>
  </section>

  <section>
    <h2>Narration ({len(audio_mix.get("narration_stems", []))})</h2>
    <table>
      <tr><th>Beat</th><th>Start</th><th>Duration</th></tr>
      {narration_rows}
    </table>
  </section>

  <section>
    <h2>Music ({len(audio_mix.get("music_stems", []))})</h2>
    <table>
      <tr><th>Cue</th><th>Track</th><th>Start</th><th>Duration</th><th>License</th><th>Selected by</th></tr>
      {"".join(music_rows)}
    </table>
  </section>
</body>
</html>
"""


def main(
    input_dir: Path,
    output_dir: Path,
    run_config: dict,
    thumbnail_extractor: ThumbnailExtractorFn | None = None,
) -> StageResponse:
    run_id = run_config["run_id"]
    timeline_path = input_dir / "timeline.json"
    audio_mix_path = input_dir / "audio_mix.json"

    missing = [p.name for p in (timeline_path, audio_mix_path) if not p.exists()]
    if missing:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(message=f"Missing required input file(s) in {input_dir}: {missing}"),
        )

    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    audio_mix = json.loads(audio_mix_path.read_text(encoding="utf-8"))
    thumbnail_extractor = thumbnail_extractor or _default_thumbnail_extractor

    thumb_dir = REPO_ROOT / "shared" / "runs" / run_id / "cache" / "thumbnails"
    thumbnails_b64: dict[str, str] = {}
    failures: list[str] = []
    for clip in timeline.get("clips", []):
        video_path = REPO_ROOT / clip["file_ref"]
        midpoint = clip["source_in_s"] + (clip["source_out_s"] - clip["source_in_s"]) / 2
        thumb_path = thumb_dir / f"{_safe_filename(clip['shot_id'])}.jpg"
        try:
            if not thumb_path.exists():
                thumbnail_extractor(video_path, midpoint, thumb_path)
            thumbnails_b64[clip["shot_id"]] = base64.b64encode(thumb_path.read_bytes()).decode("ascii")
        except (FFmpegError, OSError) as exc:
            failures.append(f"{clip['shot_id']}: {exc}")

    if failures:
        return StageResponse(
            envelope_id="",
            run_id=run_id,
            stage=STAGE_NAME,
            status=StageStatus.FAILED,
            error=ErrorInfo(
                message=f"{len(failures)} shot(s) failed thumbnail extraction - contact sheet would have missing thumbnails.",
                diagnostics="; ".join(failures),
            ),
        )

    html = _build_html(run_id, timeline.get("scene_id", ""), timeline, audio_mix, thumbnails_b64)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "contact_sheet.html").write_text(html, encoding="utf-8")

    return StageResponse(
        envelope_id="",
        run_id=run_id,
        stage=STAGE_NAME,
        status=StageStatus.COMPLETE,
        summary=(
            f"Contact sheet built: {len(timeline.get('clips', []))} shot(s), "
            f"{len(audio_mix.get('narration_stems', []))} narration stem(s), "
            f"{len(audio_mix.get('music_stems', []))} music cue(s). Awaiting human review."
        ),
        output_manifest=["outputs/contact_sheet.html"],
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
