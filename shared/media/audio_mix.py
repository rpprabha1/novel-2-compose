"""Audio mixing primitives for 09_audio_production's code half: crossfading
multiple music cues together, ducking music under narration, overlaying
narration stems, and loudness normalization. All deterministic ffmpeg
operations - CODE, not agent work (CLAUDE.md rule 4).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from .ffmpeg_utils import FFmpegError


def trim_audio(input_path: Path, duration_s: float, dest_path: Path) -> None:
    """Trims to [0, duration_s] from the start of the track. Music tracks
    (unlike video shots) have no meaningful in/out point to preserve -
    starting from 0 is the simplest deterministic choice."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(input_path), "-t", str(duration_s), str(dest_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not dest_path.exists():
        raise FFmpegError(f"audio trim failed for {input_path}: {result.stderr}")


def crossfade_concat(track_paths: list[Path], crossfade_s: float, dest_path: Path) -> None:
    """Concatenates one or more audio tracks, crossfading at each junction.
    A single track is just copied through."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if len(track_paths) == 1:
        shutil.copy(track_paths[0], dest_path)
        return

    inputs: list[str] = []
    for p in track_paths:
        inputs += ["-i", str(p)]

    filter_parts = []
    prev_label = "0:a"
    for i in range(1, len(track_paths)):
        out_label = f"cf{i}" if i < len(track_paths) - 1 else "out"
        filter_parts.append(f"[{prev_label}][{i}:a]acrossfade=d={crossfade_s}[{out_label}]")
        prev_label = out_label
    filter_complex = ";".join(filter_parts)

    result = subprocess.run(
        ["ffmpeg", "-y", *inputs, "-filter_complex", filter_complex, "-map", "[out]", str(dest_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not dest_path.exists():
        raise FFmpegError(f"crossfade concat failed: {result.stderr}")


def apply_ducking(
    music_path: Path,
    narration_windows: list[tuple[float, float]],
    depth_db: float,
    dest_path: Path,
) -> None:
    """Reduces music gain by depth_db (negative, e.g. -12) during each
    (start_s, end_s) narration window; unchanged outside those windows."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if not narration_windows:
        shutil.copy(music_path, dest_path)
        return

    filters = ",".join(
        f"volume=enable='between(t,{start},{end})':volume={depth_db}dB" for start, end in narration_windows
    )
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(music_path), "-af", filters, str(dest_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not dest_path.exists():
        raise FFmpegError(f"ducking filter failed: {result.stderr}")


def overlay_narration(music_path: Path, narration_stems: list[tuple[Path, float]], dest_path: Path) -> None:
    """Mixes narration stems onto the (already-ducked) music bed at their
    respective start_s offsets. normalize=0 on amix so ffmpeg doesn't
    auto-attenuate for input count - gain is already controlled by ducking.
    duration=longest (not first): the music track should always be trimmed
    to at least the full narration span by the caller, but this must not
    silently truncate the mix if that invariant is ever violated (e.g. a
    stale cached music file shorter than expected - this happened for real
    during development and silently cut a 64s mix down to 15.5s)."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if not narration_stems:
        shutil.copy(music_path, dest_path)
        return

    inputs: list[str] = ["-i", str(music_path)]
    delay_filters = []
    for i, (stem_path, start_s) in enumerate(narration_stems):
        inputs += ["-i", str(stem_path)]
        delay_ms = max(0, int(round(start_s * 1000)))
        delay_filters.append(f"[{i + 1}:a]adelay={delay_ms}|{delay_ms}[a{i + 1}]")

    amix_inputs = "[0:a]" + "".join(f"[a{i + 1}]" for i in range(len(narration_stems)))
    filter_complex = (
        ";".join(delay_filters)
        + f";{amix_inputs}amix=inputs={len(narration_stems) + 1}:duration=longest:dropout_transition=0:normalize=0[out]"
    )

    result = subprocess.run(
        ["ffmpeg", "-y", *inputs, "-filter_complex", filter_complex, "-map", "[out]", str(dest_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not dest_path.exists():
        raise FFmpegError(f"narration overlay failed: {result.stderr}")


def _run_loudnorm_analysis(path: Path, target_lufs: float) -> dict:
    result = subprocess.run(
        ["ffmpeg", "-i", str(path), "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", result.stderr, re.DOTALL)
    if not match:
        raise FFmpegError(f"Could not parse loudnorm measurement for {path}: {result.stderr}")
    return json.loads(match.group(0))


def measure_integrated_lufs(path: Path) -> float:
    return float(_run_loudnorm_analysis(path, -16.0)["input_i"])


def normalize_loudness(input_path: Path, target_lufs: float, dest_path: Path) -> float:
    """Two-pass loudnorm: an analysis pass measures the real input stats,
    then a linear-mode apply pass targets target_lufs using those measured
    values. Single-pass loudnorm's own gain estimate is a rough heuristic and
    was measured 1.44 LU off target on a real narration+music mix - outside
    audio_spec.yaml's tolerance_lu; two-pass is the standard fix."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    stats = _run_loudnorm_analysis(input_path, target_lufs)
    af = (
        f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11:"
        f"measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:"
        f"measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:"
        f"offset={stats['target_offset']}:linear=true"
    )
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(input_path), "-af", af, str(dest_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not dest_path.exists():
        raise FFmpegError(f"loudness normalization failed: {result.stderr}")
    return measure_integrated_lufs(dest_path)
