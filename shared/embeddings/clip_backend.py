"""CLIP embedding wrapper + on-disk cache for 04_clip_reranking.

Deterministic math (CLAUDE.md classifies this stage CODE, not agent work).
Model choice lives in config/embeddings.yaml, not hardcoded here.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import numpy as np
import requests
import torch
from PIL import Image

_model = None
_processor = None
_loaded_model_key: tuple[str, str] | None = None

# Bump this if _projected_embedding()/compute logic changes shape or meaning -
# otherwise a stale on-disk cache entry from before the change gets silently
# reused instead of recomputed (this bit us once during development).
CACHE_VERSION = "v2"


def _load_model(model_name: str, device: str):
    global _model, _processor, _loaded_model_key
    key = (model_name, device)
    if _model is None or _loaded_model_key != key:
        from transformers import CLIPModel, CLIPProcessor

        _model = CLIPModel.from_pretrained(model_name).to(device)
        _processor = CLIPProcessor.from_pretrained(model_name)
        _model.eval()
        _loaded_model_key = key
    return _model, _processor


def _projected_embedding(features) -> "torch.Tensor":
    """get_text_features()/get_image_features() return a plain projected tensor
    on some transformers versions and a BaseModelOutputWithPooling (whose
    .pooler_output is the actual projected embedding) on others - handle both."""
    if hasattr(features, "pooler_output"):
        return features.pooler_output
    return features


class EmbeddingCache:
    def __init__(
        self,
        model_name: str = "openai/clip-vit-base-patch32",
        device: str = "cpu",
        cache_dir: Path | None = None,
    ):
        self.model_name = model_name
        self.device = device
        self.cache_dir = cache_dir
        self._mem_cache: dict[str, np.ndarray] = {}
        if cache_dir is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _hash(raw: bytes) -> str:
        return hashlib.sha256(raw).hexdigest()

    def _get_or_compute(self, key: str, compute_fn) -> np.ndarray:
        if key in self._mem_cache:
            return self._mem_cache[key]
        if self.cache_dir is not None:
            path = self.cache_dir / f"{key}.npy"
            if path.exists():
                arr = np.load(path)
                self._mem_cache[key] = arr
                return arr
        arr = compute_fn()
        self._mem_cache[key] = arr
        if self.cache_dir is not None:
            np.save(self.cache_dir / f"{key}.npy", arr)
        return arr

    def embed_text(self, text: str) -> np.ndarray:
        key = f"text_{CACHE_VERSION}_" + self._hash(text.encode("utf-8"))

        def compute() -> np.ndarray:
            model, processor = _load_model(self.model_name, self.device)
            inputs = processor(text=[text], return_tensors="pt", padding=True, truncation=True).to(self.device)
            with torch.no_grad():
                features = model.get_text_features(**inputs)
            return _projected_embedding(features)[0].cpu().numpy()

        return self._get_or_compute(key, compute)

    def embed_image_bytes(self, image_bytes: bytes) -> np.ndarray:
        key = f"image_{CACHE_VERSION}_" + self._hash(image_bytes)

        def compute() -> np.ndarray:
            model, processor = _load_model(self.model_name, self.device)
            img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            inputs = processor(images=img, return_tensors="pt").to(self.device)
            with torch.no_grad():
                features = model.get_image_features(**inputs)
            return _projected_embedding(features)[0].cpu().numpy()

        return self._get_or_compute(key, compute)

    def embed_image_url(self, url: str, timeout_s: int = 20) -> np.ndarray:
        resp = requests.get(url, timeout=timeout_s)
        resp.raise_for_status()
        return self.embed_image_bytes(resp.content)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)
