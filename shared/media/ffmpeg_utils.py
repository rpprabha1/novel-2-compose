"""Thin ffmpeg/ffprobe CLI wrappers, shared across stages that touch video
(05 frame sampling, later 06 Ken Burns zoompan, 11 assembly). Deterministic
media operations - CODE per CLAUDE.md, never agent work.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


class FFmpegError(RuntimeError):
    pass


def probe_duration_s(video_path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise FFmpegError(f"ffprobe failed for {video_path}: {result.stderr}")
    data = json.loads(result.stdout)
    try:
        return float(data["format"]["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise FFmpegError(f"ffprobe returned no duration for {video_path}: {result.stdout}") from exc


def probe_resolution(video_path: Path) -> tuple[int, int]:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "json", str(video_path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise FFmpegError(f"ffprobe failed for {video_path}: {result.stderr}")
    data = json.loads(result.stdout)
    try:
        stream = data["streams"][0]
        return int(stream["width"]), int(stream["height"])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise FFmpegError(f"ffprobe returned no video stream/resolution for {video_path}: {result.stdout}") from exc


def extract_frames(video_path: Path, output_dir: Path, n_frames: int = 3) -> list[Path]:
    """Extracts n_frames evenly spaced frames (avoiding the very first/last
    instant) as jpg stills."""
    output_dir.mkdir(parents=True, exist_ok=True)
    duration = probe_duration_s(video_path)
    frame_paths: list[Path] = []
    for i in range(n_frames):
        t = duration * (i + 1) / (n_frames + 1)
        out_path = output_dir / f"frame_{i:02d}.jpg"
        result = subprocess.run(
            ["ffmpeg", "-y", "-ss", str(t), "-i", str(video_path), "-frames:v", "1", "-q:v", "2", str(out_path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not out_path.exists():
            raise FFmpegError(f"ffmpeg frame extraction failed at t={t:.2f}s for {video_path}: {result.stderr}")
        frame_paths.append(out_path)
    return frame_paths


def extract_thumbnail(video_path: Path, timestamp_s: float, dest_path: Path) -> None:
    """Grabs a single frame at an exact timestamp - unlike extract_frames(),
    the caller picks the timestamp (e.g. the midpoint of a clip's trimmed
    [source_in_s, source_out_s] window within a longer source file, not the
    midpoint of the whole file). Used by 10_human_review_gate's contact sheet."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["ffmpeg", "-y", "-ss", str(timestamp_s), "-i", str(video_path), "-frames:v", "1", "-q:v", "2", str(dest_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not dest_path.exists():
        raise FFmpegError(f"ffmpeg thumbnail extraction failed at t={timestamp_s:.2f}s for {video_path}: {result.stderr}")


def ken_burns_zoompan(
    image_path: Path,
    output_path: Path,
    duration_s: float,
    fps: int = 24,
    zoom_end: float = 1.15,
    output_size: str = "1024x1024",
) -> None:
    """Animates a still image into a video-length clip via ffmpeg's zoompan
    filter - a slow, steady zoom-in from 1.0x to zoom_end. Used by
    06_fallback_generation to turn a generated still into usable footage.

    Aspect ratio: output_size is a square crop of the source by default,
    matching sd-turbo's square output; fitting to the project's final aspect
    ratio (e.g. 16:9) is left to 11_assembly_render's compositing, not this
    helper, to keep this a one-purpose function.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frames = max(1, round(duration_s * fps))
    zoom_rate = (zoom_end - 1.0) / frames
    vf = f"scale=8000:-1,zoompan=z='min(zoom+{zoom_rate:.6f},{zoom_end})':d={frames}:s={output_size}:fps={fps}"
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-loop", "1", "-i", str(image_path),
            "-vf", vf, "-t", str(duration_s), "-pix_fmt", "yuv420p", str(output_path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not output_path.exists():
        raise FFmpegError(f"ffmpeg Ken Burns zoompan failed for {image_path}: {result.stderr}")


def generate_mood_visual(
    dest_path: Path,
    duration_s: float,
    width: int,
    height: int,
    fps: int,
    color1: str,
    color2: str,
    zoom_end: float = 1.12,
) -> None:
    """Ken-Burns-animated abstract gradient - 06_fallback_generation's
    default visual for beats with no matched footage, replacing the earlier
    on-screen text card (2026-07-23, see ARCHITECTURE.md change log): the
    narration is already spoken aloud by TTS, so also displaying that same
    text as a card is redundant and reads as a broken slideshow rather than
    video - a real defect the author flagged directly, not a hypothetical.
    Pure ffmpeg (a still gradient frame, then the existing ken_burns_zoompan
    helper already used for the sd-turbo AGENT-mode path) - no agent, no
    model, matching this stage's CODE classification.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    still_path = dest_path.with_suffix(".still.png")
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"gradients=s={width}x{height}:d=1:c0={color1}:c1={color2}:type=radial",
            "-frames:v", "1", str(still_path),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not still_path.exists():
        raise FFmpegError(f"ffmpeg mood-gradient still generation failed for {dest_path}: {result.stderr}")
    try:
        ken_burns_zoompan(still_path, dest_path, duration_s, fps=fps, zoom_end=zoom_end, output_size=f"{width}x{height}")
    finally:
        still_path.unlink(missing_ok=True)


