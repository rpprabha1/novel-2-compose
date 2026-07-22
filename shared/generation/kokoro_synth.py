"""Kokoro TTS synthesis entrypoint - runs under Python 3.12, not the
pipeline's 3.9 (kokoro-onnx needs onnxruntime>=1.20.1 which needs py>=3.10;
this machine has 3.12 available via the py launcher). Invoked as a
subprocess by tts_backend.py the same way Piper already is - the pipeline
itself never imports this under 3.9.

Usage: py -3.12 kokoro_synth.py <text_file> <out_wav> <model_path> <voices_path> <voice> <speed>
"""

import sys
from pathlib import Path


def main() -> int:
    text_file, out_wav, model_path, voices_path, voice, speed = sys.argv[1:7]
    text = Path(text_file).read_text(encoding="utf-8")

    import soundfile as sf
    from kokoro_onnx import Kokoro

    kokoro = Kokoro(model_path, voices_path)
    samples, sample_rate = kokoro.create(text, voice=voice, speed=float(speed), lang="en-us")
    sf.write(out_wav, samples, sample_rate)
    return 0


if __name__ == "__main__":
    sys.exit(main())
