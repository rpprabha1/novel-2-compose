"""Pixel-art restyling primitive for 13_pixel_art_conversion: area-averaged
downscale to a pixel grid (each block's true averaged color, not one
arbitrary sampled pixel - see the 2026-07-18 fix below), palette reduction
with ordered dithering, an edge-detected dark outline overlay on real object
boundaries (color preserved - only the luma channel is darkened), then
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
    edge_low: float,
    edge_high: float,
    output_width: int,
    output_height: int,
    video_codec: str,
    crf: int,
    audio_codec: str,
    audio_bitrate: str,
    dest_path: Path,
) -> None:
    """Downscales to grid_width x grid_height with area averaging (fixed
    2026-07-18: the original nearest-neighbor downscale point-sampled one
    arbitrary pixel per block instead of averaging the region, which made
    object boundaries read as arbitrary/unclear in places - area averaging
    gives each block its region's true dominant color), reduces to
    max_colors via palettegen/paletteuse (dither_method, e.g. bayer, gives
    the retro color-speckle look instead of flat color banding), overlays a
    dark outline traced from edgedetect at edge_low/edge_high sensitivity
    (computed at the same low grid resolution so the outline aligns to the
    pixel blocks, and darkened into the luma channel only so the underlying
    color survives), then upscales back to output_width x output_height with
    nearest-neighbor so pixel edges stay hard. Audio is passed through
    unchanged - this stage only restyles video."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    filter_complex = (
        f"[0:v]scale={grid_width}:{grid_height}:flags=area,split=3[a][b][c];"
        f"[a]palettegen=max_colors={max_colors}:stats_mode={palettegen_stats_mode}[p];"
        f"[b][p]paletteuse=dither={dither_method}:bayer_scale={bayer_scale},format=yuv420p[pal];"
        f"[c]edgedetect=mode=wires:low={edge_low}:high={edge_high},negate,format=gray[edges];"
        f"[pal]extractplanes=y+u+v[bY][bU][bV];"
        f"[bY][edges]blend=all_mode=multiply:all_opacity=1[outY];"
        f"[outY][bU][bV]mergeplanes=0x001020:yuv420p[low];"
        f"[low]scale={output_width}:{output_height}:flags=neighbor[v]"
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
