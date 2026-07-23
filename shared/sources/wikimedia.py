"""WikimediaCommonsSource: FootageSource over the Wikimedia Commons API.

Approved in LICENSES.md since 2026-07-14 but never implemented until
2026-07-23 (see ARCHITECTURE.md change log) - added to widen the legal
footage pool after a real run left ~40% of beats on text-card fallbacks.

License handling per LICENSES.md's Commons row: license varies per file
(CC0/PD/CC-BY/CC-BY-SA), captured exactly from extmetadata at search time;
attribution is required for everything except CC0/PD marks. CC-BY-SA's
share-alike obligation is surfaced via the license string itself for the
human review pass - this module never filters SA out silently.
"""

from __future__ import annotations

import re

import requests

from .base import FootageCandidate

API_URL = "https://commons.wikimedia.org/w/api.php"
# Commons requires a descriptive UA with contact info for API etiquette.
USER_AGENT = "novel2compose/1.0 (https://github.com/; rpprabha1@gmail.com)"

_NO_ATTRIBUTION_LICENSES = {"cc0", "public domain", "pd"}
_MAX_FILE_BYTES = 150 * 1024 * 1024  # skip enormous originals; sampling a 90-min film isn't viable


def _strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value or "").strip()


class WikimediaCommonsSource:
    # Must match candidates.schema.json's `source` enum value exactly
    # ("wikimedia_commons", not "wikimedia") - a real run crashed on schema
    # validation the first time a query actually returned a Wikimedia hit
    # (see ARCHITECTURE.md 2026-07-23 change log entry).
    name = "wikimedia_commons"

    def __init__(self, timeout_s: int = 30):
        self.timeout_s = timeout_s

    def search(self, query: str, max_results: int = 5) -> list[FootageCandidate]:
        resp = requests.get(
            API_URL,
            headers={"User-Agent": USER_AGENT},
            params={
                "action": "query",
                "format": "json",
                "generator": "search",
                "gsrsearch": f"filetype:video {query}",
                "gsrnamespace": 6,
                # Over-fetch: some hits get dropped below (wrong mime, too large).
                "gsrlimit": max_results * 2,
                "prop": "imageinfo",
                "iiprop": "url|size|mime|extmetadata",
            },
            timeout=self.timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
        pages = (data.get("query") or {}).get("pages") or {}

        # generator=search returns pages keyed by pageid in arbitrary dict
        # order; the search rank is in each page's "index" field.
        candidates: list[FootageCandidate] = []
        for page in sorted(pages.values(), key=lambda p: p.get("index", 0)):
            infos = page.get("imageinfo") or []
            if not infos:
                continue
            ii = infos[0]
            if not (ii.get("mime") or "").startswith("video/"):
                continue
            if (ii.get("size") or 0) > _MAX_FILE_BYTES:
                continue
            extmeta = ii.get("extmetadata") or {}
            license_name = _strip_html((extmeta.get("LicenseShortName") or {}).get("value") or "") or "unknown"
            creator = _strip_html((extmeta.get("Artist") or {}).get("value") or "") or None
            duration = ii.get("duration")
            page_id = page.get("pageid")
            candidates.append(
                FootageCandidate(
                    candidate_id=f"wikimedia_{page_id}",
                    source=self.name,
                    url=ii.get("descriptionurl", ""),
                    license=license_name,
                    thumbnail_ref=ii.get("url", ""),
                    download_url=ii.get("url"),
                    duration_s=float(duration) if duration is not None else None,
                    creator=creator,
                    requires_attribution=license_name.lower() not in _NO_ATTRIBUTION_LICENSES,
                )
            )
            if len(candidates) >= max_results:
                break
        return candidates
