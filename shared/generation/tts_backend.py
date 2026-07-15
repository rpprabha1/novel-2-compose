"""Local TTS (piper) for 09_audio_production's code half - synthesizes an
already-written narration text (the source manuscript's own prose, extracted
verbatim per beat). This module never writes or alters narration text.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class TTSError(RuntimeError):
    pass


def synthesize_speech(
    text: str,
    dest_path: Path,
    model_path: Path,
    config_path: Path,
    length_scale: float = 1.0,
) -> None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "piper",
            "-m", str(model_path),
            "-c", str(config_path),
            "-f", str(dest_path),
            "--length-scale", str(length_scale),
        ],
        input=text,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not dest_path.exists():
        raise TTSError(f"piper TTS synthesis failed: {result.stderr}")
