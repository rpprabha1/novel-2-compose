"""MusicSource contract - mirrors FootageSource (base.py) but for music.

No approved source currently has a public search API (see LICENSES.md,
corrected 2026-07-14: Pixabay Music and Mixkit both lack one). Until one
exists, a ManualMusicSource (curated candidate list from a human/manual web
search, same methodology as Gate 0) is the only real implementation; this
Protocol exists so Stage 09's code doesn't change shape when a real API does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class MusicCandidate:
    track_ref: str
    source: str
    url: str
    license: str
    download_url: str | None = None
    duration_s: float | None = None
    creator: str | None = None
    requires_attribution: bool = False


class MusicSource(Protocol):
    name: str

    def search(self, mood_tags: list[str], max_results: int) -> list[MusicCandidate]: ...
