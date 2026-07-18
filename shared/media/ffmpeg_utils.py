"""Thin ffmpeg/ffprobe CLI wrappers, shared across stages that touch video
(05 frame sampling, later 06 Ken Burns zoompan, 11 assembly). Deterministic
media operations - CODE per CLAUDE.md, never agent work.
"""

from __future__ import annotations

import json
import subprocess
import textwrap
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


def generate_text_card(
    text: str,
    duration_s: float,
    dest_path: Path,
    width: int,
    height: int,
    fps: int,
    bg_color: str,
    text_color: str,
    font_path: str,
    font_size: int,
    max_chars_per_line: int,
) -> None:
    """Lightweight ffmpeg-only fallback visual: wrapped text over a solid
    background, rendered directly as a video-length clip - no diffusion
    model, no still-image intermediate. 06_fallback_generation's default
    CODE path (see ARCHITECTURE.md 2026-07-18) for beats with no matched
    footage, chosen after sd-turbo repeatedly exhausted RAM/disk on a
    constrained dev machine; this has no such risk since it's pure ffmpeg."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    wrapped = textwrap.fill(text, width=max_chars_per_line)
    textfile_path = dest_path.with_suffix(".txt")
    textfile_path.write_text(wrapped, encoding="utf-8")
    try:
        # drawtext's textfile=/fontfile= both need literal colons (Windows
        # drive letters) escaped for the ffmpeg filtergraph parser.
        textfile_arg = str(textfile_path).replace("\\", "/").replace(":", "\\:")
        fontfile_arg = font_path.replace("\\", "/").replace(":", "\\:")
        vf = (
            f"drawtext=textfile='{textfile_arg}':fontfile='{fontfile_arg}':"
            f"fontcolor={text_color}:fontsize={font_size}:line_spacing=12:"
            f"x=(w-text_w)/2:y=(h-text_h)/2:box=1:boxcolor=black@0.45:boxborderw=24"
        )
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={bg_color}:s={width}x{height}:d={duration_s}:r={fps}",
                "-vf", vf, "-pix_fmt", "yuv420p", str(dest_path),
            ],
            capture_output=True,
            text=True,
        )
    finally:
        textfile_path.unlink(missing_ok=True)
    if result.returncode != 0 or not dest_path.exists():
        raise FFmpegError(f"ffmpeg text card generation failed for {dest_path}: {result.stderr}")
