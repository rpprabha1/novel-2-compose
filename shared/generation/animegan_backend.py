"""AnimeGANv2 video restyling for 14_anime_style_conversion.

Deterministic execution of an already-trained, already-chosen model - no
judgment happens here (CLAUDE.md rule 4): the human picked the checkpoint
(paprika) and the uniform-application policy after reviewing real rendered
samples (see ARCHITECTURE.md change log), same process already used for
Stage 13's pixel-art technique. This module just runs that fixed choice.

Frame-by-frame neural inference is expensive on a CPU-only machine (real
measurement: ~1.2s/frame at 960x544 - see ARCHITECTURE.md change log), so
stylization runs at a reduced frame rate (config's stylize_fps) and the
result is frame-duplicated back up to the final output fps - not a quality
compromise so much as a real animation convention (limited/"on-twos"
animation is standard practice in actual anime production, not just
expedient here), and is what makes a full-chapter run tractable at all.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_model = None
_loaded_checkpoint: str | None = None


class AnimeGANError(RuntimeError):
    pass


def _load_model(checkpoint_path: Path, device: str):
    global _model, _loaded_checkpoint
    key = str(checkpoint_path)
    if _model is None or _loaded_checkpoint != key:
        sys.path.insert(0, str(REPO_ROOT / "shared" / "models" / "animegan"))
        import torch
        from model import Generator  # noqa: E402 (vendored architecture, see that module's docstring)

        torch_device = torch.device(device)
        model = Generator().to(torch_device)
        state_dict = torch.load(checkpoint_path, map_location=torch_device)
        model.load_state_dict(state_dict)
        model.eval()
        _model = model
        _loaded_checkpoint = key
    return _model


def _extract_frames(src_path: Path, frames_dir: Path, width: int, fps: float) -> int:
    frames_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(src_path),
            "-vf", f"fps={fps},scale={width}:-2:flags=lanczos",
            str(frames_dir / "frame_%06d.png"),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise AnimeGANError(f"ffmpeg frame extraction failed for {src_path}: {result.stderr}")
    return len(list(frames_dir.glob("frame_*.png")))


def _style_frames(frames_dir: Path, styled_dir: Path, model, device: str) -> None:
    import torch
    from PIL import Image
    from torchvision.transforms.functional import to_pil_image, to_tensor

    styled_dir.mkdir(parents=True, exist_ok=True)
    for frame_path in sorted(frames_dir.glob("frame_*.png")):
        img = Image.open(frame_path).convert("RGB")
        with torch.no_grad():
            inp = (to_tensor(img).unsqueeze(0) * 2 - 1).to(device)
            out = model(inp).cpu()[0]
        styled = to_pil_image((out * 0.5 + 0.5).clip(0, 1))
        styled.save(styled_dir / frame_path.name)


def _reassemble(styled_dir: Path, src_path: Path, stylize_fps: float, output_fps: int, dest_path: Path, video_codec: str, video_crf: int, audio_codec: str, audio_bitrate: str) -> None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-framerate", str(stylize_fps), "-i", str(styled_dir / "frame_%06d.png"),
            "-i", str(src_path),
            "-map", "0:v:0", "-map", "1:a:0",
            "-vf", f"fps={output_fps}",
            "-c:v", video_codec, "-crf", str(video_crf), "-pix_fmt", "yuv420p",
            "-c:a", audio_codec, "-b:a", audio_bitrate,
            "-shortest", str(dest_path),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not dest_path.exists():
        raise AnimeGANError(f"ffmpeg reassembly failed for {dest_path}: {result.stderr}")


def stylize_video(
    src_path: Path,
    dest_path: Path,
    checkpoint_path: Path,
    device: str,
    target_width: int,
    stylize_fps: float,
    output_fps: int,
    video_codec: str,
    video_crf: int,
    audio_codec: str,
    audio_bitrate: str,
    work_dir: Path,
) -> None:
    """Extracts frames at (target_width, stylize_fps), runs each through the
    AnimeGANv2 generator, reassembles at output_fps with the source audio.
    work_dir holds intermediate frames and is removed on success; left in
    place on failure so a crash mid-run can be inspected/resumed."""
    model = _load_model(checkpoint_path, device)
    frames_dir = work_dir / "raw_frames"
    styled_dir = work_dir / "styled_frames"

    n = _extract_frames(src_path, frames_dir, target_width, stylize_fps)
    if n == 0:
        raise AnimeGANError(f"No frames extracted from {src_path}")

    _style_frames(frames_dir, styled_dir, model, device)
    _reassemble(styled_dir, src_path, stylize_fps, output_fps, dest_path, video_codec, video_crf, audio_codec, audio_bitrate)

    shutil.rmtree(work_dir, ignore_errors=True)
