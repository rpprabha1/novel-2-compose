# shared/embeddings/

CLIP embedding wrapper + cache, used exclusively by `04_clip_reranking` to score candidate thumbnails against a beat's `visual_description`. Deterministic math (CLAUDE.md §2's classification for Stage 04), not agent work.

**Implemented.** Backend: HuggingFace `transformers` CLIP (`openai/clip-vit-base-patch32` by default — config in `config/embeddings.yaml`, same "fit the dev machine" reasoning as `config/agents.yaml`: CPU-only, ~8GB RAM).

```python
class EmbeddingCache:
    def embed_text(self, text: str) -> np.ndarray: ...
    def embed_image_bytes(self, image_bytes: bytes) -> np.ndarray: ...
    def embed_image_url(self, url: str, timeout_s: int = 20) -> np.ndarray: ...
    # Cache key: sha256 of the input (text or image bytes). In-memory always;
    # also persisted to cache_dir/*.npy if a cache_dir is given, so repeated
    # runs against the same candidate/beat pair don't recompute.

def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float: ...
```

`config/thresholds.yaml`'s `clip_reranking.similarity_cutoff` and `close_score_margin` are the only place routing thresholds are defined — this module returns raw scores, and `04`'s `run.py` does the routing.
