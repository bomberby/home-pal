import threading
from pathlib import Path

import torch
from compel import Compel
from diffusers import StableDiffusionPipeline, DPMSolverMultistepScheduler, AutoencoderKL

from agents.persona_states import CHARACTER_PREFIX

MODEL_ID = "Lykon/dreamshaper-8"
VAE_ID = "stabilityai/sd-vae-ft-mse"
FIXED_SEED = 42
OUTPUT_DIR = Path("tmp/persona")
PROMPT_SUFFIX = ", soft lighting, clean background"
NEGATIVE_PROMPT = (
    "(worst quality:1.4), (low quality:1.4), (normal quality:1.3), (lowres:1.3), blurry, jpeg artifacts, "
    "(bad anatomy:1.5), (deformed:1.4), (disfigured:1.4), (mutated:1.4), (malformed:1.4), "
    "(extra limbs:1.4), (missing limbs:1.3), (floating limbs:1.4), (disconnected limbs:1.4), "
    "(extra arms:1.4), (extra legs:1.4), (fused body parts:1.3), "
    "(bad hands:1.5), (malformed hands:1.5), (mutated hands:1.5), (poorly drawn hands:1.4), "
    "(extra fingers:1.5), (missing fingers:1.5), (fused fingers:1.5), (too many fingers:1.5), (wrong number of fingers:1.5), "
    "(four fingers:1.8), (3 fingers:1.8), (two fingers:1.8), (too few fingers:1.7), (missing finger:1.7), "
    "(segmented fingers:1.6), (jointed fingers:1.5), (broken fingers:1.5), (disconnected finger:1.6), "
    "(deformed face:1.4), (disfigured face:1.4), (malformed face:1.3), (bad face:1.3), "
    "(cross-eyed:1.2), (asymmetric eyes:1.2), "
    "multiple people, duplicate, clone, watermark, signature, text, username, writing, letters, words, error, "
    "(discolored nails:1.4), (bad nails:1.3), (deformed nails:1.3)"
)

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
TORCH_DTYPE = torch.float16 if DEVICE == "cuda" else torch.float32

if DEVICE == "cpu":
    print("WARNING: CUDA is not available. Image generation will run on CPU and be very slow.")


class ImageGenService:
    _pipeline = None
    _compel = None
    _lock = threading.Lock()
    _in_progress: set[str] = set()

    @classmethod
    def _get_pipeline(cls):
        if cls._pipeline is None:
            print(f"Loading image generation pipeline on {DEVICE}...")
            vae = AutoencoderKL.from_pretrained(VAE_ID, torch_dtype=TORCH_DTYPE).to(DEVICE)
            cls._pipeline = StableDiffusionPipeline.from_pretrained(
                MODEL_ID,
                vae=vae,
                torch_dtype=TORCH_DTYPE,
                safety_checker=None,
            ).to(DEVICE)
            cls._pipeline.scheduler = DPMSolverMultistepScheduler.from_config(
                cls._pipeline.scheduler.config,
                use_karras_sigmas=True,
                algorithm_type="dpmsolver++",
            )
            cls._pipeline.enable_attention_slicing()
            cls._compel = Compel(
                tokenizer=cls._pipeline.tokenizer,
                text_encoder=cls._pipeline.text_encoder,
            )
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
                _hand_triggers = {"hand", "finger", "wave", "hold", "keyboard", "typing", "fanning", "umbrella", "mug", "drink"}
                if _hand_triggers & set(full_prompt.lower().split()):
                    full_prompt += ", delicate silver ring on her finger, five fingers"
                print(f"[ImageGen] Generating '{state}'")
                print(f"[ImageGen] Prompt: {full_prompt}")
                prompt_embeds = cls._compel(full_prompt)
                negative_embeds = cls._compel(NEGATIVE_PROMPT)

                image = pipe(
                    prompt_embeds=prompt_embeds,
                    negative_prompt_embeds=negative_embeds,
                    num_inference_steps=20,
                    guidance_scale=6.5,
                    clip_skip=2,
                    generator=generator,
                    width=512,
                    height=512,
                ).images[0]

                image.save(output_path)
        finally:
            cls._in_progress.discard(state)

        return output_path
