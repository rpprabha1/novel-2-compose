"""Local TTS for 09_audio_production's code half - synthesizes an
already-written narration text (the source manuscript's own prose, extracted
verbatim per beat). This module never writes or alters narration text.

Two engines, selected by config/tts.yaml's `engine` key:
- "piper": the original backend (external `piper` CLI, ONNX voice models).
- "kokoro": Kokoro-82M via kokoro-onnx (added 2026-07-23, see ARCHITECTURE.md
  change log - author found Piper's lessac voice too robotic on the real
  chapter-1 output). Runs under Python 3.12 via the `py` launcher in a
  subprocess (kokoro-onnx needs onnxruntime>=1.20.1 which needs py>=3.10;
  the pipeline itself runs on 3.9) - architecturally the same shape as the
  piper CLI call, just a different external process.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

_KOKORO_SYNTH_SCRIPT = Path(__file__).resolve().parent / "kokoro_synth.py"


class TTSError(RuntimeError):
    pass


def synthesize_speech(
    text: str,
    dest_path: Path,
    model_path: Path,
    config_path: Path,
    length_scale: float = 1.0,
) -> None:
    """Piper engine (original backend)."""
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


def synthesize_speech_kokoro(
    text: str,
    dest_path: Path,
    model_path: Path,
    voices_path: Path,
    voice: str,
    speed: float = 1.0,
    python_launcher: str = "py -3.12",
) -> None:
    """Kokoro engine. Text goes via a temp file (not argv) so arbitrary prose
    punctuation/length never hits Windows command-line quoting limits."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as tf:
        tf.write(text)
        text_file = tf.name
    try:
        result = subprocess.run(
            [
                *python_launcher.split(),
                str(_KOKORO_SYNTH_SCRIPT),
                text_file,
                str(dest_path),
                str(model_path),
                str(voices_path),
                voice,
                str(speed),
            ],
            capture_output=True,
            text=True,
        )
    finally:
        Path(text_file).unlink(missing_ok=True)
    if result.returncode != 0 or not dest_path.exists():
        raise TTSError(f"kokoro TTS synthesis failed: {result.stderr}")
