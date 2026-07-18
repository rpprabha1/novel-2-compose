"""Pixel-art restyling primitive for 13_pixel_art_conversion: nearest-neighbor
downscale to a pixel grid, palette reduction with ordered dithering, then
nearest-neighbor upscale back to source resolution. Deterministic ffmpeg
filter chain - CODE, not agent work (CLAUDE.md).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .ffmpeg_utils import FFmpegError


def apply_pixel_art_style(
    src_path: Path,
    grid_width: int,
    grid_height: int,
    max_colors: int,
    palettegen_stats_mode: str,
    dither_method: str,
    bayer_scale: int,
    output_width: int,
    output_height: int,
    video_codec: str,
    crf: int,
    audio_codec: str,
    audio_bitrate: str,
    dest_path: Path,
) -> None:
    """Downscales to grid_width x grid_height with nearest-neighbor (the
    blocky pixel grid), reduces to max_colors via palettegen/paletteuse
    (dither_method, e.g. bayer, gives the retro color-speckle look instead of
    flat color banding), then upscales back to output_width x output_height
    with nearest-neighbor so pixel edges stay hard rather than blurring back
    out. Audio is passed through unchanged - this stage only restyles video."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    filter_complex = (
        f"[0:v]scale={grid_width}:{grid_height}:flags=neighbor,split[a][b];"
        f"[a]palettegen=max_colors={max_colors}:stats_mode={palettegen_stats_mode}[p];"
        f"[b][p]paletteuse=dither={dither_method}:bayer_scale={bayer_scale}[pal];"
        f"[pal]scale={output_width}:{output_height}:flags=neighbor[v]"
    )
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(src_path),
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "0:a",
            "-c:v", video_codec, "-crf", str(crf), "-pix_fmt", "yuv420p",
            "-c:a", audio_codec, "-b:a", audio_bitrate,
            str(dest_path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not dest_path.exists():
        raise FFmpegError(f"ffmpeg pixel-art conversion failed for {src_path}: {result.stderr}")
