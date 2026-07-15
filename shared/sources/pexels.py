from __future__ import annotations

import requests

from .base import FootageCandidate

SEARCH_URL = "https://api.pexels.com/v1/videos/search"


class PexelsSource:
    name = "pexels"
    license_text = "Pexels License"

    def __init__(self, api_key: str, timeout_s: int = 20):
        self.api_key = api_key
        self.timeout_s = timeout_s

    def search(self, query: str, max_results: int = 5) -> list[FootageCandidate]:
        resp = requests.get(
            SEARCH_URL,
            headers={"Authorization": self.api_key},
            params={"query": query, "per_page": max_results},
            timeout=self.timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()

        candidates: list[FootageCandidate] = []
        for video in data.get("videos", []):
            duration = video.get("duration")
            candidates.append(
                FootageCandidate(
                    candidate_id=f"pexels_{video['id']}",
                    source=self.name,
                    url=video.get("url", ""),
                    license=self.license_text,
                    thumbnail_ref=video.get("image", ""),
                    download_url=self._pick_download_url(video.get("video_files", [])),
                    duration_s=float(duration) if duration is not None else None,
                    creator=(video.get("user") or {}).get("name"),
                )
            )
        return candidates

    @staticmethod
    def _pick_download_url(video_files: list[dict]) -> str | None:
        """Smallest mp4 file with width >= 480 (fast to download for frame-sampling
        verification purposes), falling back to the smallest available mp4."""
        mp4_files = [f for f in video_files if f.get("file_type") == "video/mp4" and f.get("link")]
        if not mp4_files:
            return None
        mp4_files.sort(key=lambda f: f.get("width") or 0)
        for f in mp4_files:
            if (f.get("width") or 0) >= 480:
                return f["link"]
        return mp4_files[0]["link"]
