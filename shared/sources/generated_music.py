"""GeneratedMusicSource: a MusicSource that synthesizes a mood-appropriate
audio bed via ffmpeg rather than fetching a third-party track.

Not a stand-in for real stock search - LICENSES.md already lists "Generated /
composed audio" as a pre-approved category ("Output of the pipeline's own
generation step, not a third-party fetch", no attribution, no restriction).
Used here because no approved MusicSource has a public search API (see
music_base.py) and hand-curating real Mixkit/Pixabay tracks for every mood
combination across an entire novel's chapters isn't practical for a bulk run
- see DECISIONS_LOG.md. A real curated pass can replace any cue's track
later; this keeps the mixing pipeline (ducking, crossfade, LUFS
normalization) exercised end-to-end for real in the meantime.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .music_base import MusicCandidate

# tag -> (base_freq_hz, tremolo_hz, tremolo_depth). Not a creative/final
# choice, just a deterministic, distinguishable placeholder character per
# mood so different cues in the same scene don't all sound identical.
_MOOD_SOUND = {
    "tense": (220.0, 6.0, 0.5),
    "quiet": (180.0, 0.2, 0.1),
    "ominous": (80.0, 0.3, 0.3),
    "sparse": (150.0, 0.1, 0.05),
    "triumphant": (330.0, 1.0, 0.15),
    "somber": (110.0, 0.15, 0.2),
    "playful": (392.0, 4.0, 0.25),
    "romantic": (294.0, 0.5, 0.15),
    "urgent": (250.0, 8.0, 0.4),
    "angry": (200.0, 10.0, 0.5),
    "calm": (196.0, 0.1, 0.05),
    "curious": (262.0, 2.0, 0.2),
    "omniscient": (174.0, 0.2, 0.1),
    "protective": (147.0, 0.3, 0.15),
    "sad": (130.0, 0.2, 0.2),
    "social": (277.0, 1.5, 0.2),
    "wise": (165.0, 0.25, 0.15),
    "cynical": (233.0, 3.0, 0.3),
}
_DEFAULT_SOUND = (220.0, 1.0, 0.15)
_GENERATED_DURATION_S = 600.0  # trimmed down to the real cue length later


class GeneratedMusicSource:
    name = "generated"

    def search(self, mood_tags: list[str], max_results: int = 3) -> list[MusicCandidate]:
        seen: set[str] = set()
        results: list[MusicCandidate] = []
        tags = mood_tags or ["_default"]
        for tag in tags:
            track_ref = f"generated_{tag}"
            if track_ref in seen:
                continue
            seen.add(track_ref)
            freq, tremolo, depth = _MOOD_SOUND.get(tag, _DEFAULT_SOUND)
            results.append(
                MusicCandidate(
                    track_ref=track_ref,
                    source="generated",
                    url=f"generated://{tag}",
                    license="Generated / composed audio",
                    # Trailing ".wav" isn't decorative - Stage 09's downloader
                    # dispatch does Path(download_url).suffix to name the raw
                    # cache file, and a bare "...:0.5" pseudo-URL makes that
                    # resolve to ".5" (the last dot-decimal), not a real
                    # extension - broke for real, caught during orchestrator
                    # smoke test, see DECISIONS_LOG.md.
                    download_url=f"generated://{tag}:{freq}:{tremolo}:{depth}.wav",
                    duration_s=None,
                    creator=None,
                    requires_attribution=False,
                )
            )
            if len(results) >= max_results:
                break
        return results


def generated_audio_downloader(pseudo_url: str, dest: Path) -> None:
    """Stage 09's `downloader` callable for GeneratedMusicSource candidates -
    interprets `download_url` as a synthesis spec (generated://tag:freq:tremolo:depth.wav)
    instead of fetching over HTTP, and renders directly to dest via ffmpeg."""
    body = pseudo_url.removeprefix("generated://").removesuffix(".wav")
    _tag, freq_s, tremolo_s, depth_s = body.rsplit(":", 3)
    freq, tremolo, depth = float(freq_s), float(tremolo_s), float(depth_s)
    dest.parent.mkdir(parents=True, exist_ok=True)
    expr = f"0.35*sin(2*PI*{freq}*t)*(1-{depth}+{depth}*sin(2*PI*{tremolo}*t))"
    fade_out_start = max(0.0, _GENERATED_DURATION_S - 2.0)
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"aevalsrc={expr}:s=44100:d={_GENERATED_DURATION_S}",
            "-af", f"afade=t=in:st=0:d=2,afade=t=out:st={fade_out_start}:d=2",
            "-ac", "2", str(dest),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not dest.exists():
        raise RuntimeError(f"ffmpeg generated-audio synthesis failed for {pseudo_url}: {result.stderr}")
