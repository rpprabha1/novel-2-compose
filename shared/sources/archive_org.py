"""ArchiveOrgSource: FootageSource over the Internet Archive's APIs.

Approved in LICENSES.md since 2026-07-14 ("Archive.org public domain") but
never implemented until 2026-07-23 (see ARCHITECTURE.md change log).

Guardrails learned from real API probing before implementation (not
hypothetical): a naive downloads-sorted mediatype:movies query surfaces
full-length feature films with zero relevance to the query, so this source
(a) restricts to explicit public-domain license URLs per LICENSES.md's
"public domain" scope, (b) uses relevance ordering rather than popularity,
(c) skips items whose smallest mp4 derivative is still enormous - Stage 05
downloads candidates whole for frame sampling, and a multi-GB feature film
is not a viable "clip". One metadata call per hit is needed to find the
actual mp4 file (the search API only returns item identifiers).
"""

from __future__ import annotations

import requests

from .base import FootageCandidate

SEARCH_URL = "https://archive.org/advancedsearch.php"
METADATA_URL = "https://archive.org/metadata/{identifier}"
DOWNLOAD_URL = "https://archive.org/download/{identifier}/{filename}"

_MAX_FILE_BYTES = 200 * 1024 * 1024
_MP4_FORMATS = ("512Kb MPEG4", "MPEG4", "h.264", "h.264 IA", "HiRes MPEG4")


class ArchiveOrgSource:
    name = "archive_org"
    license_text = "Public Domain (archive.org)"

    def __init__(self, timeout_s: int = 30):
        self.timeout_s = timeout_s

    def search(self, query: str, max_results: int = 5) -> list[FootageCandidate]:
        resp = requests.get(
            SEARCH_URL,
            params={
                "q": f"mediatype:movies AND licenseurl:*publicdomain* AND ({query})",
                "fl[]": ["identifier", "title", "creator"],
                "rows": max_results * 2,
                "output": "json",
            },
            timeout=self.timeout_s,
        )
        resp.raise_for_status()
        docs = (resp.json().get("response") or {}).get("docs") or []

        candidates: list[FootageCandidate] = []
        for doc in docs:
            identifier = doc.get("identifier")
            if not identifier:
                continue
            picked = self._pick_mp4(identifier)
            if picked is None:
                continue
            filename, duration_s = picked
            creator = doc.get("creator")
            if isinstance(creator, list):
                creator = creator[0] if creator else None
            candidates.append(
                FootageCandidate(
                    candidate_id=f"archiveorg_{identifier}",
                    source=self.name,
                    url=f"https://archive.org/details/{identifier}",
                    license=self.license_text,
                    thumbnail_ref=f"https://archive.org/services/img/{identifier}",
                    download_url=DOWNLOAD_URL.format(identifier=identifier, filename=filename),
                    duration_s=duration_s,
                    creator=creator,
                    requires_attribution=False,
                )
            )
            if len(candidates) >= max_results:
                break
        return candidates

    def _pick_mp4(self, identifier: str) -> tuple[str, float | None] | None:
        """Smallest usable mp4 derivative for this item, or None if the item
        has no reasonably-sized mp4 at all."""
        try:
            resp = requests.get(METADATA_URL.format(identifier=identifier), timeout=self.timeout_s)
            resp.raise_for_status()
            files = resp.json().get("files") or []
        except (requests.RequestException, ValueError):
            return None

        mp4s = []
        for f in files:
            if f.get("format") not in _MP4_FORMATS or not f.get("name"):
                continue
            try:
                size = int(f.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            if size == 0 or size > _MAX_FILE_BYTES:
                continue
            try:
                duration = float(f["length"]) if f.get("length") else None
            except (TypeError, ValueError):
                duration = None
            mp4s.append((size, f["name"], duration))
        if not mp4s:
            return None
        mp4s.sort(key=lambda t: t[0])
        _, name, duration = mp4s[0]
        return name, duration
