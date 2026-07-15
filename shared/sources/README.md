# shared/sources/

Interface contracts + implementations for asset acquisition. Every stage that fetches an asset (`03_candidate_fetch`, `06_fallback_generation`'s render step, `09_audio_production`'s code half) goes through one of these interfaces — no stage calls a provider API directly (CLAUDE.md §9).

## `FootageSource`

`base.py` defines the Protocol + `FootageCandidate` dataclass. `search()` only — Stage 03 captures metadata/thumbnail references, not full video downloads (that's Stage 05, downloading just the top-k winners per beat).

```python
class FootageSource(Protocol):
    name: str  # must match a source key in LICENSES.md

    def search(self, query: str, max_results: int) -> list[FootageCandidate]: ...
```

**Implemented:** `PexelsSource` (`pexels.py`, needs `PEXELS_API_KEY`), `PixabaySource` (`pixabay.py`, needs `PIXABAY_API_KEY`). Both verified against their real API response shapes (Pexels `GET /v1/videos/search`, Pixabay `GET /api/videos/`).

**Not yet implemented:** Mixkit, Coverr (no official public API per `LICENSES.md` — manual/curated only), Archive.org, Wikimedia Commons, NASA, author's library. Add these the same way: `LICENSES.md` row + `ARCHITECTURE.md` change-log entry already exist for the first five; implement a module here when a stage actually needs that source's coverage.

## `MusicSource` (planned interface)

One implementation per approved music source (Pixabay Music, Mixkit, generated/composed).

```python
class MusicSource(Protocol):
    name: str  # must match a source key in LICENSES.md

    def search(self, mood_tags: list[str], max_results: int) -> list[MusicCandidate]: ...
    def fetch(self, candidate_id: str, dest_path: Path) -> FetchResult: ...
```

## Rules

- Adding a source here requires the `LICENSES.md` row and `ARCHITECTURE.md` change-log entry to exist first (CLAUDE.md §0, "New sources require...").
- `search`/`fetch` are the only judgment-free operations here — any "which source to prefer" or "is this a good match" logic belongs upstream (04_clip_reranking, 09's agent half), not in a source adapter.
- API keys are read from `config/.env` (see `config/.env.example`), never hardcoded.
