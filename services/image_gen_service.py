import threading
from pathlib import Path

import torch
from diffusers import StableDiffusionPipeline

from agents.persona_states import CHARACTER_PREFIX

MODEL_ID = "Lykon/dreamshaper-8"
FIXED_SEED = 42
OUTPUT_DIR = Path("tmp/persona")
PROMPT_SUFFIX = ", soft lighting, clean background"
NEGATIVE_PROMPT = (
    "blurry, deformed, extra limbs, bad anatomy, multiple people, duplicate, "
    "watermark, signature, text, lowres, worst quality, low quality"
)

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
TORCH_DTYPE = torch.float16 if DEVICE == "cuda" else torch.float32

if DEVICE == "cpu":
    print("WARNING: CUDA is not available. Image generation will run on CPU and be very slow.")


class ImageGenService:
    _pipeline = None
    _lock = threading.Lock()
    _in_progress: set[str] = set()

    @classmethod
    def _get_pipeline(cls):
        if cls._pipeline is None:
            print(f"Loading image generation pipeline on {DEVICE}...")
            cls._pipeline = StableDiffusionPipeline.from_pretrained(
                MODEL_ID,
                torch_dtype=TORCH_DTYPE,
                safety_checker=None,
            ).to(DEVICE)
            cls._pipeline.enable_attention_slicing()
        return cls._pipeline

    @classmethod
    def get_cached(cls, state: str) -> Path | None:
        path = OUTPUT_DIR / f"{state}.png"
        return path if path.exists() else None

    @classmethod
    def generate(cls, state: str, scene_prompt: str) -> Path:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = OUTPUT_DIR / f"{state}.png"

        cls._in_progress.add(state)
        try:
            with cls._lock:
                pipe = cls._get_pipeline()
                generator = torch.Generator(DEVICE).manual_seed(FIXED_SEED)
                full_prompt = f"{CHARACTER_PREFIX}, {scene_prompt}{PROMPT_SUFFIX}"

                image = pipe(
                    full_prompt,
                    negative_prompt=NEGATIVE_PROMPT,
                    num_inference_steps=25,
                    guidance_scale=7.5,
                    generator=generator,
                    width=512,
                    height=512,
                ).images[0]

                image.save(output_path)
        finally:
            cls._in_progress.discard(state)

        return output_path
