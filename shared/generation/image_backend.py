"""Local diffusion image generation for 06_fallback_generation's code half.

Deterministic-enough execution of an already-written prompt - this module
takes a prompt and renders it, it does not write the prompt (that's the
agent half). Model choice lives in config/image_gen.yaml, not hardcoded here.
"""

from __future__ import annotations

from pathlib import Path

_pipe = None
_loaded_model_key: tuple[str, str] | None = None


def _load_pipeline(model_name: str, device: str):
    global _pipe, _loaded_model_key
    key = (model_name, device)
    if _pipe is None or _loaded_model_key != key:
        import torch
        from diffusers import AutoPipelineForText2Image

        # variant="fp16" selects the smaller on-disk checkpoint files (~2.6GB
        # vs. ~5GB for sd-turbo's fp32 files). torch_dtype=torch.float16
        # (changed from float32 2026-07-18) keeps compute in fp16 too, not
        # just storage - upcasting each fp16 tensor to float32 on load means
        # peak memory briefly approaches the *full-precision* ~5GB footprint
        # even when reading the smaller files, which is what actually
        # exhausted RAM/pagefile on a constrained dev machine for real
        # (segfault/OOM partway through loading, confirmed independent of
        # available disk space). Staying in float16 throughout keeps the
        # real memory footprint near the ~2.6GB file size instead.
        _pipe = AutoPipelineForText2Image.from_pretrained(model_name, variant="fp16", torch_dtype=torch.float16)
        _pipe.to(device)
        _loaded_model_key = key
    return _pipe


def generate_image(
    prompt: str,
    negative_prompt: str,
    dest_path: Path,
    model: str = "stabilityai/sd-turbo",
    device: str = "cpu",
    num_inference_steps: int = 2,
    guidance_scale: float = 0.0,
    image_size: int = 512,
) -> None:
    pipe = _load_pipeline(model, device)
    kwargs = {
        "prompt": prompt,
        "num_inference_steps": num_inference_steps,
        "guidance_scale": guidance_scale,
        "height": image_size,
        "width": image_size,
    }
    # sd-turbo is a distilled few-step model and doesn't support classifier-free
    # guidance (guidance_scale must stay 0.0), so a negative_prompt has no
    # effect there; pass it through anyway for models where it does.
    if guidance_scale > 0.0:
        kwargs["negative_prompt"] = negative_prompt
    image = pipe(**kwargs).images[0]
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest_path)
