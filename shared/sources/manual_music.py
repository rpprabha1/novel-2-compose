"""ManualMusicSource: a MusicSource backed by a human-curated candidate list,
for use until an approved source has a real search API (see music_base.py).
Candidates are researched the same way as Gate 0's manual coverage test -
by hand, via each source's web UI - and passed in at construction time.
"""

from __future__ import annotations

from .music_base import MusicCandidate


class ManualMusicSource:
    name = "manual"

    def __init__(self, candidates_by_tag: dict[str, list[MusicCandidate]]):
        """candidates_by_tag: maps a single mood tag to the candidates a human
        found for it. search() unions candidates across every requested tag
        that has an entry, de-duplicated by track_ref."""
        self.candidates_by_tag = candidates_by_tag

    def search(self, mood_tags: list[str], max_results: int = 3) -> list[MusicCandidate]:
        seen: set[str] = set()
        results: list[MusicCandidate] = []
        for tag in mood_tags:
            for candidate in self.candidates_by_tag.get(tag, []):
                if candidate.track_ref in seen:
                    continue
                seen.add(candidate.track_ref)
                results.append(candidate)
                if len(results) >= max_results:
                    return results
        return results
