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

        _pipe = AutoPipelineForText2Image.from_pretrained(model_name, torch_dtype=torch.float32)
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
