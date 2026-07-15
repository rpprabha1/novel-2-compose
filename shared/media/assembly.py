"""Video assembly primitives for 11_assembly_render: normalizing mixed-source
clips to one canvas, concatenating with transitions, and muxing the final
audio track. All deterministic ffmpeg operations - CODE, not agent work.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .ffmpeg_utils import FFmpegError, probe_duration_s


def normalize_clip(
    src_path: Path,
    source_in_s: float,
    source_out_s: float,
    width: int,
    height: int,
    fps: int,
    video_codec: str,
    crf: int,
    dest_path: Path,
) -> None:
    """Trims to [source_in_s, source_out_s], scales to fit within
    width x height preserving aspect ratio, and pads with black
    (letterbox/pillarbox - never crops or distorts). Drops any audio the
    source clip has - the final mix comes entirely from 09_audio_production's
    scene_mix.wav, never a stock clip's own embedded audio track."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    duration = source_out_s - source_in_s
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={fps}"
    )
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-ss", str(source_in_s), "-i", str(src_path), "-t", str(duration),
            "-vf", vf, "-an", "-c:v", video_codec, "-crf", str(crf), "-pix_fmt", "yuv420p",
            str(dest_path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not dest_path.exists():
        raise FFmpegError(f"clip normalization failed for {src_path}: {result.stderr}")


def concat_hard_cut(a_path: Path, b_path: Path, video_codec: str, crf: int, dest_path: Path) -> None:
    """Instant cut - used for hard-cut and match-cut-suggestion (a match cut
    is a compositional match, not a timed blend)."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(a_path), "-i", str(b_path),
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[outv]",
            "-map", "[outv]", "-c:v", video_codec, "-crf", str(crf), "-pix_fmt", "yuv420p",
            str(dest_path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not dest_path.exists():
        raise FFmpegError(f"hard-cut concat failed: {result.stderr}")


def xfade_transition(
    a_path: Path, b_path: Path, a_duration_s: float, transition_duration_s: float,
    video_codec: str, crf: int, dest_path: Path,
) -> None:
    """Crossfades the last transition_duration_s of a_path into the first
    transition_duration_s of b_path. This BORROWS time from each clip's own
    span rather than adding extra runtime - the combined output is
    (a_duration + b_duration - transition_duration_s) long, matching how
    professional editors budget a crossfade (see 11_assembly_render/README.md
    for why this matters for staying in sync with the audio-driven timeline)."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    offset = max(0.0, a_duration_s - transition_duration_s)
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(a_path), "-i", str(b_path),
            "-filter_complex",
            f"[0:v][1:v]xfade=transition=fade:duration={transition_duration_s}:offset={offset}[outv]",
            "-map", "[outv]", "-c:v", video_codec, "-crf", str(crf), "-pix_fmt", "yuv420p",
            str(dest_path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not dest_path.exists():
        raise FFmpegError(f"crossfade transition failed: {result.stderr}")


def dip_to_black_transition(
    a_path: Path, b_path: Path, fade_duration_s: float, video_codec: str, crf: int, dest_path: Path,
) -> None:
    """Fades the end of a_path to black and the start of b_path in from
    black, then hard-cuts the two together. Unlike a crossfade this is an
    in-place effect on each clip's own existing time - it does not borrow
    time from either side, so total runtime is unaffected (a_duration + b_duration)."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    a_duration = probe_duration_s(a_path)
    fade_start = max(0.0, a_duration - fade_duration_s)
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(a_path), "-i", str(b_path),
            "-filter_complex",
            f"[0:v]fade=t=out:st={fade_start}:d={fade_duration_s}[a];"
            f"[1:v]fade=t=in:st=0:d={fade_duration_s}[b];"
            f"[a][b]concat=n=2:v=1:a=0[outv]",
            "-map", "[outv]", "-c:v", video_codec, "-crf", str(crf), "-pix_fmt", "yuv420p",
            str(dest_path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not dest_path.exists():
        raise FFmpegError(f"dip-to-black transition failed: {result.stderr}")


def match_duration(video_path: Path, target_duration_s: float, dest_path: Path) -> None:
    """Pads (freeze-frame extend) or trims video_path so its duration
    exactly matches target_duration_s - used to reconcile the video track
    (shortened slightly by any crossfades) with the audio-driven timeline
    before the final mux, rather than leaving mismatched stream lengths for
    a player to resolve however it likes."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    current = probe_duration_s(video_path)
    diff = target_duration_s - current
    if abs(diff) < 0.01:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", str(video_path), "-c", "copy", str(dest_path)],
            capture_output=True, text=True,
        )
    elif diff > 0:
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(video_path),
                "-vf", f"tpad=stop_mode=clone:stop_duration={diff}",
                "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                str(dest_path),
            ],
            capture_output=True, text=True,
        )
    else:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", str(video_path), "-t", str(target_duration_s), "-c", "copy", str(dest_path)],
            capture_output=True, text=True,
        )
    if result.returncode != 0 or not dest_path.exists():
        raise FFmpegError(f"duration matching failed for {video_path}: {result.stderr}")


def mux_video_audio(video_path: Path, audio_path: Path, audio_codec: str, audio_bitrate: str, dest_path: Path) -> None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(video_path), "-i", str(audio_path),
            "-map", "0:v", "-map", "1:a", "-c:v", "copy",
            "-c:a", audio_codec, "-b:a", audio_bitrate,
            str(dest_path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not dest_path.exists():
        raise FFmpegError(f"final mux failed: {result.stderr}")
