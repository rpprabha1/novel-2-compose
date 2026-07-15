# shared/generation/

Local diffusion image backend for `06_fallback_generation`'s code half. Deterministic execution of an already-written prompt (from the agent half / `AGENT_PROMPT.md`) — this module never writes or modifies a prompt.

**Backend:** HuggingFace `diffusers`, `stabilityai/sd-turbo` by default (`config/image_gen.yaml` — same "fit the dev machine" reasoning as `config/agents.yaml`/`config/embeddings.yaml`: CPU-only, ~8GB RAM). SD-Turbo is a distilled few-step model; 2 inference steps produced good-quality 512x512 stills in ~35-60s on this machine's CPU, vs. minutes for a full 20-50 step SD pipeline. `guidance_scale` must stay `0.0` for `sd-turbo` (it doesn't support classifier-free guidance) — the code only passes `negative_prompt` through when `guidance_scale > 0`, for compatibility with other models later.

```python
generate_image(prompt: str, negative_prompt: str, dest_path: Path, model: str, device: str, num_inference_steps: int, guidance_scale: float, image_size: int) -> None
```

Model is loaded once per process and cached (module-level, like `shared/embeddings/`'s CLIP model).
