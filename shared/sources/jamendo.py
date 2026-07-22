"""JamendoMusicSource: MusicSource over the Jamendo API (api.jamendo.com).

Added 2026-07-23 (LICENSES.md row + ARCHITECTURE.md change log entry in the
same change): the first approved music source with a real search API -
Pixabay Music and Mixkit both lack one (verified live against Pixabay's API
2026-07-22, consistent with LICENSES.md's corrected 2026-07-14 finding), so
until now the pipeline's only automated option was the generated sine-bed
placeholder. Jamendo tracks are CC-licensed (per-track variant captured
from the API response); CC licenses require attribution, so every candidate
sets requires_attribution=True and carries the artist name for CREDITS.md.

Mood search: Jamendo's fuzzytags matches loosely against its own tag folk-
sonomy; this source passes the pipeline's mood tags through directly plus
"instrumental" (vocal music under narration is a mixing hazard), ordered by
Jamendo's popularity ranking.
"""

from __future__ import annotations

import requests

from .music_base import MusicCandidate

API_URL = "https://api.jamendo.com/v3.0/tracks/"


class JamendoMusicSource:
    name = "jamendo"

    def __init__(self, client_id: str, timeout_s: int = 30, tag_map: dict[str, str] | None = None):
        """tag_map: pipeline mood tag -> Jamendo folksonomy tags (from
        config/audio_spec.yaml's jamendo_tag_map). Without it, our mood
        vocabulary mostly isn't in Jamendo's tag folksonomy and fuzzytags
        silently degrades to overall popularity - verified live: three
        different mood queries returned identical tracks. Space-separated
        (never '+'-joined: requests URL-encodes '+' to a literal plus, which
        Jamendo doesn't treat as a separator - also caught live)."""
        self.client_id = client_id
        self.timeout_s = timeout_s
        self.tag_map = tag_map or {}

    def search(self, mood_tags: list[str], max_results: int = 3) -> list[MusicCandidate]:
        # Progressively broader queries: many combined tag words can narrow
        # fuzzytags to zero hits (observed live: 'tense'+'urgent' mapped to 5
        # words and returned nothing). An imperfect-mood real track beats a
        # no_music_candidates fallback (no music at all) for that cue.
        attempts: list[list[str]] = []
        full: list[str] = []
        for tag in mood_tags:
            for word in self.tag_map.get(tag, tag).split():
                if word not in full:
                    full.append(word)
        attempts.append([*full, "instrumental"])
        if mood_tags:
            first_only = self.tag_map.get(mood_tags[0], mood_tags[0]).split()
            if set(first_only) != set(full):
                attempts.append([*first_only, "instrumental"])
        attempts.append(["cinematic", "instrumental"])

        results: list[dict] = []
        for words in attempts:
            resp = requests.get(
                API_URL,
                params={
                    "client_id": self.client_id,
                    "format": "json",
                    "limit": max_results,
                    "fuzzytags": " ".join(words),
                    "order": "popularity_total",
                    "audioformat": "mp32",
                    "include": "licenses",
                },
                timeout=self.timeout_s,
            )
            resp.raise_for_status()
            results = resp.json().get("results") or []
            if results:
                break

        candidates: list[MusicCandidate] = []
        for track in results:
            if not track.get("audiodownload_allowed", True) and not track.get("audio"):
                continue
            download_url = track.get("audiodownload") or track.get("audio")
            if not download_url:
                continue
            try:
                duration = float(track["duration"]) if track.get("duration") else None
            except (TypeError, ValueError):
                duration = None
            candidates.append(
                MusicCandidate(
                    track_ref=f"jamendo_{track['id']}",
                    source=self.name,
                    url=track.get("shareurl", ""),
                    license=track.get("license_ccurl") or "CC (variant unspecified - verify before release)",
                    download_url=download_url,
                    duration_s=duration,
                    creator=track.get("artist_name"),
                    requires_attribution=True,
                )
            )
        return candidates
