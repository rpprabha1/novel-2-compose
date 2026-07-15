"""FootageSource contract. One implementation per approved source in LICENSES.md.

Stage 03 only calls search() - it captures metadata/thumbnails, not full video
downloads (that's Stage 05's job, downloading just the top-k winners). Adding a
new source means: add its LICENSES.md row + ARCHITECTURE.md change-log entry
first (CLAUDE.md rule 0), then a module here implementing this Protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class FootageCandidate:
    candidate_id: str
    source: str
    url: str
    license: str
    thumbnail_ref: str
    download_url: str | None = None  # direct video file URL, if the source provides one
    duration_s: float | None = None
    creator: str | None = None
    # Per LICENSES.md - Pexels/Pixabay require no attribution; a future source
    # with attribution terms (e.g. Wikimedia CC-BY) must set this True explicitly.
    requires_attribution: bool = False


class FootageSource(Protocol):
    name: str

    def search(self, query: str, max_results: int) -> list[FootageCandidate]: ...
