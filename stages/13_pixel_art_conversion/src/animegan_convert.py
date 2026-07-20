"""Standalone AnimeGANv2 video converter.

Downloads bryandlee/animegan2-pytorch weights via torch.hub, processes each
frame through the model on MPS (M-series Mac GPU) or CPU, and reassembles
with the original audio via ffmpeg.

Usage:
    python3 animegan_convert.py <input.mp4> <output.mp4> [--style STYLE]

Styles (pretrained weights available):
    face_paint_512_v2   (default) — painterly anime, good for natural scenes
    paprika             — Paprika film style
    celeba_distill      — lighter, faster
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image


def get_device() -> torch.device:
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def load_model(style: str, device: torch.device):
    print(f"Loading AnimeGANv2 model ({style}) on {device}...")
    model = torch.hub.load(
        "bryandlee/animegan2-pytorch:main",
        "generator",
        pretrained=style,
        progress=True,
        trust_repo=True,
    )
    model.to(device).eval()
    return model


@torch.inference_mode()
def convert_frame(frame_bgr: np.ndarray, model, device: torch.device) -> np.ndarray:
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(rgb)

    # Model expects float32 in [-1, 1], shape (1, C, H, W)
    t = torch.from_numpy(np.array(pil, dtype=np.float32) / 127.5 - 1.0)
    t = t.permute(2, 0, 1).unsqueeze(0).to(device)

    out = model(t).squeeze(0).permute(1, 2, 0).cpu().numpy()
    out = ((out + 1.0) * 127.5).clip(0, 255).astype(np.uint8)
    return cv2.cvtColor(out, cv2.COLOR_RGB2BGR)


def extract_audio(src: Path, audio_path: Path) -> bool:
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-vn", "-c:a", "copy", str(audio_path)],
        capture_output=True,
    )
    return r.returncode == 0 and audio_path.exists()


def mux_audio(frames_path: Path, audio_path: Path | None, fps: float, dest: Path) -> None:
    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", str(frames_path / "frame_%06d.png"),
    ]
    if audio_path and audio_path.exists():
        cmd += ["-i", str(audio_path), "-c:a", "aac", "-b:a", "192k", "-shortest"]
    cmd += ["-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", str(dest)]
    subprocess.run(cmd, check=True, capture_output=True)


def main(src: Path, dest: Path, style: str) -> None:
    device = get_device()
    model = load_model(style, device)

    cap = cv2.VideoCapture(str(src))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"Source: {src.name}  fps={fps:.2f}  frames={total}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        frames_dir = tmp_dir / "frames"
        frames_dir.mkdir()

        audio_path = tmp_dir / "audio.aac"
        has_audio = extract_audio(src, audio_path)

        idx = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            converted = convert_frame(frame, model, device)
            cv2.imwrite(str(frames_dir / f"frame_{idx:06d}.png"), converted)
            idx += 1
            if idx % 30 == 0:
                print(f"  {idx}/{total} frames done")

        cap.release()
        print(f"Converted {idx} frames — assembling video...")
        mux_audio(frames_dir, audio_path if has_audio else None, fps, dest)

    print(f"Done -> {dest}  ({dest.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--style", default="face_paint_512_v2")
    args = ap.parse_args()
    main(Path(args.input), Path(args.output), args.style)
