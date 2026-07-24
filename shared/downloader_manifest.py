"""Build a source-free metadata manifest for the downloader stage's outputs.

The downloader (`stages/01_1_downloader/`) drops video clips into its
`outputs/` folder. Downstream pipeline stages need a manifest to pick those
clips up, but this manifest deliberately records ONLY technical facts about
each file (dimensions, duration, codec, size) plus a neutral clip id. It
NEVER records where a clip came from - no origin, platform, url, channel,
uploader, creator, or license - so nothing about the source is attached
anywhere in the pipeline.

This module only ever reads the downloader's `outputs/` folder and probes the
media files there with ffprobe. It does not read or depend on the downloader's
own code in any way.

Usage:
    python -m shared.downloader_manifest                 # default outputs dir
    python -m shared.downloader_manifest <outputs_dir>   # explicit dir
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUTS_DIR = REPO_ROOT / "stages" / "01_1_downloader" / "outputs"
MANIFEST_NAME = "downloader_manifest.json"
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}


def _ffprobe(path: Path) -> dict:
    """Return ffprobe's JSON for one file (streams + format)."""
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration,size",
            "-show_entries", "stream=codec_type,codec_name,width,height,r_frame_rate",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path.name}: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def _fps(rate: str | None) -> float | None:
    if not rate or rate == "0/0":
        return None
    num, _, den = rate.partition("/")
    try:
        den_f = float(den) if den else 1.0
        return round(float(num) / den_f, 3) if den_f else None
    except ValueError:
        return None


def _probe_clip(path: Path, clip_id: str) -> dict:
    data = _ffprobe(path)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    fmt = data.get("format", {})
    duration = float(fmt["duration"]) if fmt.get("duration") else None

    # file_ref locates the file for downstream stages; it necessarily contains
    # the filename, but carries no source/origin metadata.
    return {
        "clip_id": clip_id,
        "file_ref": str(path.relative_to(REPO_ROOT)).replace("\\", "/"),
        "duration_s": round(duration, 3) if duration is not None else None,
        "width": video.get("width"),
        "height": video.get("height"),
        "fps": _fps(video.get("r_frame_rate")),
        "video_codec": video.get("codec_name"),
        "has_audio": has_audio,
        "filesize_bytes": int(fmt["size"]) if fmt.get("size") else None,
    }


def build_manifest(outputs_dir: Path = DEFAULT_OUTPUTS_DIR) -> dict:
    """Scan *outputs_dir* for video files and build the source-free manifest."""
    clips = []
    files = sorted(p for p in outputs_dir.iterdir() if p.suffix.lower() in VIDEO_EXTS)
    for i, path in enumerate(files, 1):
        clips.append(_probe_clip(path, clip_id=f"clip_{i:03d}"))
    return {
        "stage": "01_1_downloader",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "clip_count": len(clips),
        "clips": clips,
    }


def write_manifest(outputs_dir: Path = DEFAULT_OUTPUTS_DIR) -> Path:
    manifest = build_manifest(outputs_dir)
    dest = outputs_dir / MANIFEST_NAME
    dest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return dest


if __name__ == "__main__":
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUTS_DIR
    written = write_manifest(out_dir)
    print(f"Wrote {written} ({json.loads(written.read_text(encoding='utf-8'))['clip_count']} clip(s))")
