"""Mechanical (non-agent) stock-footage search-term extraction.

Moved here from stages/03_candidate_fetch/src/run.py 2026-07-23 (see
ARCHITECTURE.md change log) so Stage 02 can also use it - as a deterministic
fallback when the agent-written `search_query` field repeats identically
across many beats (CLAUDE.md rule 2: no stage imports another stage's src;
shared logic lives here instead). CODE, no judgment: lowercase, drop
stopwords and short words, de-duplicate, keep the first max_terms in
reading order.
"""

from __future__ import annotations

import re

_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "of", "in", "on", "at", "to", "from",
    "with", "her", "his", "she", "he", "it", "its", "their", "they", "is",
    "are", "was", "were", "as", "into", "onto", "toward", "towards", "behind",
    "beside", "under", "over", "through", "across", "down", "up", "out",
    "for", "that", "this", "these", "those", "who", "which", "one", "only",
    "them", "there", "than", "then", "so", "if", "be", "been", "being",
}


def extract_search_terms(visual_description: str, max_terms: int = 8) -> str:
    """Lowercase, drop stopwords and short words, de-duplicate, keep the
    first max_terms in reading order."""
    words = re.findall(r"[A-Za-z']+", visual_description.lower())
    seen: set[str] = set()
    terms: list[str] = []
    for w in words:
        if w in _STOPWORDS or len(w) <= 2 or w in seen:
            continue
        seen.add(w)
        terms.append(w)
        if len(terms) >= max_terms:
            break
    return " ".join(terms)
