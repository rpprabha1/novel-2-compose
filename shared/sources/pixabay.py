from __future__ import annotations

import requests

from .base import FootageCandidate

SEARCH_URL = "https://pixabay.com/api/videos/"


class PixabaySource:
    name = "pixabay"
    license_text = "Pixabay License"

    def __init__(self, api_key: str, timeout_s: int = 20):
        self.api_key = api_key
        self.timeout_s = timeout_s

    def search(self, query: str, max_results: int = 5) -> list[FootageCandidate]:
        # Pixabay requires per_page in [3, 200]; request the minimum viable amount
        # and trim to what was actually asked for.
        per_page = max(max_results, 3)
        resp = requests.get(
            SEARCH_URL,
            params={"key": self.api_key, "q": query, "per_page": per_page},
            timeout=self.timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()

        candidates: list[FootageCandidate] = []
        for hit in data.get("hits", [])[:max_results]:
            videos = hit.get("videos", {})
            # "small" preferred over "medium"/"large" - fast to download for
            # frame-sampling verification purposes (same reasoning as Pexels).
            preferred = videos.get("small") or videos.get("medium") or videos.get("tiny") or videos.get("large") or {}
            candidates.append(
                FootageCandidate(
                    candidate_id=f"pixabay_{hit['id']}",
                    source=self.name,
                    url=hit.get("pageURL", ""),
                    license=self.license_text,
                    thumbnail_ref=preferred.get("thumbnail", ""),
                    download_url=preferred.get("url") or None,
                    duration_s=float(hit["duration"]) if hit.get("duration") is not None else None,
                    creator=hit.get("user"),
                )
            )
        return candidates
