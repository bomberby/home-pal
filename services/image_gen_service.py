import threading
import time
import warnings
from collections import deque
from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F
from compel import Compel
from diffusers import StableDiffusionPipeline, DPMSolverMultistepScheduler, AutoencoderKL

from agents.persona_states import CHARACTER_PREFIX

MODEL_ID = "Lykon/dreamshaper-8"
VAE_ID = "stabilityai/sd-vae-ft-mse"
FIXED_SEED = 42
OUTPUT_DIR = Path("tmp/persona")
PROMPT_SUFFIX = ", soft lighting, clean background"
PROMPT_SUFFIX_DARK = ", clean background"
DARK_NEGATIVE = (
    "lamp, artificial lighting, bright interior, warm indoor lighting, "
    "ceiling light, light on, illuminated room, indoor light source"
)
NEGATIVE_PROMPT = (
    "(worst quality:1.4), (low quality:1.4), (normal quality:1.3), (lowres:1.3), blurry, jpeg artifacts, "
    "(bad anatomy:1.5), (deformed:1.4), (disfigured:1.4), (mutated:1.4), (malformed:1.4), (merged limbs:1.4), (limbs merging with objects:1.4), (arm becoming object:1.4), "
    "(extra limbs:1.4), (missing limbs:1.3), (floating limbs:1.4), (disconnected limbs:1.4), "
    "(extra arms:1.4), (extra legs:1.4), (fused body parts:1.3), "
    "(bad hands:1.5), (malformed hands:1.5), (mutated hands:1.5), (poorly drawn hands:1.4), "
    "(extra fingers:1.8), (missing fingers:1.7), (fused fingers:1.7), (too many fingers:1.8), (wrong number of fingers:1.8), (six fingers:1.9), (seven fingers:1.9), "
    "(four fingers:1.8), (3 fingers:1.8), (two fingers:1.8), (too few fingers:1.8), (missing finger:1.8), "
    "(segmented fingers:1.6), (jointed fingers:1.6), (broken fingers:1.6), (disconnected finger:1.6), "
    "(deformed face:1.4), (disfigured face:1.4), (malformed face:1.3), (bad face:1.3), "
    "(cross-eyed:1.2), (asymmetric eyes:1.2), "
    "multiple people, duplicate, clone, watermark, signature, text, username, writing, letters, words, error, "
    "(discolored nails:1.4), (bad nails:1.3), (deformed nails:1.3)"
)

# Real-ESRGAN weights for final upscale after refinement
_REALESRGAN_WEIGHTS_URL = (
    "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth"
)
_REALESRGAN_WEIGHTS_PATH = Path("env/weights/RealESRGAN_x4plus_anime_6B.pth")

# HQ regeneration: same prompt + seed, higher resolution
_REFINE_SIZE = 768   # HQ generation resolution (512 → initial display, 768 → background HQ)

# Final output size — HQ image is resized here with LANCZOS before saving
_FINAL_SIZE = 1024

warnings.filterwarnings("ignore", message="Token indices sequence length is longer than")

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
TORCH_DTYPE = torch.float16 if DEVICE == "cuda" else torch.float32

if DEVICE == "cpu":
    print("WARNING: CUDA is not available. Image generation will run on CPU and be very slow.")


# ------------------------------------------------------------------ #
#  Minimal RRDB network for Real-ESRGAN — no basicsr dependency       #
# ------------------------------------------------------------------ #

class _ResidualDenseBlock(nn.Module):
    def __init__(self, num_feat=64, num_grow_ch=32):
        super().__init__()
        self.conv1 = nn.Conv2d(num_feat, num_grow_ch, 3, 1, 1)
        self.conv2 = nn.Conv2d(num_feat + num_grow_ch, num_grow_ch, 3, 1, 1)
        self.conv3 = nn.Conv2d(num_feat + 2 * num_grow_ch, num_grow_ch, 3, 1, 1)
        self.conv4 = nn.Conv2d(num_feat + 3 * num_grow_ch, num_grow_ch, 3, 1, 1)
        self.conv5 = nn.Conv2d(num_feat + 4 * num_grow_ch, num_feat, 3, 1, 1)
        self.lrelu = nn.LeakyReLU(negative_slope=0.2, inplace=True)

    def forward(self, x):
        x1 = self.lrelu(self.conv1(x))
        x2 = self.lrelu(self.conv2(torch.cat((x, x1), 1)))
        x3 = self.lrelu(self.conv3(torch.cat((x, x1, x2), 1)))
        x4 = self.lrelu(self.conv4(torch.cat((x, x1, x2, x3), 1)))
        x5 = self.conv5(torch.cat((x, x1, x2, x3, x4), 1))
        return x5 * 0.2 + x


class _RRDB(nn.Module):
    def __init__(self, num_feat, num_grow_ch=32):
        super().__init__()
        self.rdb1 = _ResidualDenseBlock(num_feat, num_grow_ch)
        self.rdb2 = _ResidualDenseBlock(num_feat, num_grow_ch)
        self.rdb3 = _ResidualDenseBlock(num_feat, num_grow_ch)

    def forward(self, x):
        out = self.rdb3(self.rdb2(self.rdb1(x)))
        return out * 0.2 + x


class _RRDBNet(nn.Module):
    def __init__(self, num_in_ch, num_out_ch, scale, num_feat, num_block, num_grow_ch=32):
        super().__init__()
        self.conv_first = nn.Conv2d(num_in_ch, num_feat, 3, 1, 1)
        self.body = nn.Sequential(*[_RRDB(num_feat, num_grow_ch) for _ in range(num_block)])
        self.conv_body = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        self.conv_up1  = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        self.conv_up2  = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        self.conv_hr   = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        self.conv_last = nn.Conv2d(num_feat, num_out_ch, 3, 1, 1)
        self.lrelu = nn.LeakyReLU(negative_slope=0.2, inplace=True)

    def forward(self, x):
        feat = self.conv_first(x)
        feat = feat + self.conv_body(self.body(feat))
        feat = self.lrelu(self.conv_up1(F.interpolate(feat, scale_factor=2, mode='nearest')))
        feat = self.lrelu(self.conv_up2(F.interpolate(feat, scale_factor=2, mode='nearest')))
        return self.conv_last(self.lrelu(self.conv_hr(feat)))


class ImageGenService:
    _pipeline: StableDiffusionPipeline | None = None
    _compel: Compel | None = None
    _lock = threading.Lock()
    _in_progress: set[str] = set()

    # HQ upgrade scheduler
    # Queue stores (state_key, scene_prompt | None).
    # scene_prompt is None for images queued at startup — those get upscale-only.
    _upscaler: _RRDBNet | None = None
    _upgrade_queue: deque = deque()  # deque[tuple[str, str | None]]
    _upgrade_in_progress: set[str] = set()
    _upgrade_scheduler_started: bool = False

    # ------------------------------------------------------------------ #
    #  Standard generation                                                 #
    # ------------------------------------------------------------------ #

    @classmethod
    def _get_pipeline(cls) -> StableDiffusionPipeline:
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
                truncate_long_prompts=False,
            )
        return cls._pipeline

    @classmethod
    def get_cached(cls, state: str) -> Path | None:
        """Return HQ path if available, otherwise standard path."""
        hq = cls._hq_path(state)
        if hq.exists():
            return hq
        std = OUTPUT_DIR / f"{state}.png"
        return std if std.exists() else None

    @classmethod
    def generate(cls, state: str, scene_prompt: str) -> Path:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = OUTPUT_DIR / f"{state}.png"

        cls._in_progress.add(state)
        try:
            with cls._lock:
                pipe = cls._get_pipeline()
                generator = torch.Generator(DEVICE).manual_seed(FIXED_SEED)
                is_dark = state.endswith("_dark")
                suffix = PROMPT_SUFFIX_DARK if is_dark else PROMPT_SUFFIX
                negative = f"{NEGATIVE_PROMPT}, {DARK_NEGATIVE}" if is_dark else NEGATIVE_PROMPT
                full_prompt = f"{CHARACTER_PREFIX}, {scene_prompt}{suffix}"
                _hand_triggers = {"hand", "finger", "wave", "hold", "keyboard", "typing", "fanning", "umbrella", "mug", "drink"}
                if any(t in full_prompt.lower() for t in _hand_triggers):
                    full_prompt += ", delicate silver ring on her finger, (five fingers:1.5)"
                print(f"[ImageGen] Generating '{state}'" + (" (dark)" if is_dark else ""))
                print(f"[ImageGen] Prompt: {full_prompt}")
                prompt_embeds = cls._compel(full_prompt)
                negative_embeds = cls._compel(negative)
                [prompt_embeds, negative_embeds] = cls._compel.pad_conditioning_tensors_to_same_length([prompt_embeds, negative_embeds])

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

        # Queue for HQ refinement — store scene_prompt so _refine() can reconstruct the full prompt
        if not cls._hq_path(state).exists() and state not in cls._upgrade_in_progress:
            cls._upgrade_queue.append((state, scene_prompt))

        return output_path

    # ------------------------------------------------------------------ #
    #  HQ upgrade scheduler                                               #
    # ------------------------------------------------------------------ #

    @classmethod
    def _hq_path(cls, state: str) -> Path:
        return OUTPUT_DIR / f"{state}_hq.png"

    @classmethod
    def start_upgrade_scheduler(cls):
        if cls._upgrade_scheduler_started:
            return
        cls._upgrade_scheduler_started = True

        if OUTPUT_DIR.exists():
            # Existing images have no stored prompt — queue with None (upscale-only fallback)
            pending = [
                (p.stem, None) for p in OUTPUT_DIR.glob("*.png")
                if not p.stem.endswith("_hq") and not cls._hq_path(p.stem).exists()
            ]
            cls._upgrade_queue.extend(pending)
            if pending:
                print(f"[ImageGen] Upgrade scheduler started — {len(pending)} existing images queued.")
            else:
                print("[ImageGen] Upgrade scheduler started.")

        threading.Thread(target=cls._upgrade_loop, daemon=True).start()

    @classmethod
    def _upgrade_loop(cls):
        while True:
            if cls._upgrade_queue and not cls._in_progress:
                state, scene_prompt = cls._upgrade_queue.popleft()
                if state in cls._upgrade_in_progress or cls._hq_path(state).exists():
                    continue
                std_path = OUTPUT_DIR / f"{state}.png"
                if not std_path.exists():
                    continue
                cls._upgrade_in_progress.add(state)
                try:
                    if scene_prompt is not None:
                        cls._refine(state, scene_prompt, std_path)
                    else:
                        cls._upscale(state, std_path)
                finally:
                    cls._upgrade_in_progress.discard(state)
            else:
                time.sleep(5)

    @classmethod
    def _refine(cls, state: str, scene_prompt: str, src: Path):
        """Regenerate from the original prompt at _REFINE_SIZE for a clean HQ image."""
        print(f"[ImageGen] Regenerating '{state}' at {_REFINE_SIZE}×{_REFINE_SIZE}...")
        try:
            with cls._lock:
                pipe = cls._get_pipeline()
                generator = torch.Generator(DEVICE).manual_seed(FIXED_SEED)
                is_dark = state.endswith("_dark")
                suffix = PROMPT_SUFFIX_DARK if is_dark else PROMPT_SUFFIX
                negative = f"{NEGATIVE_PROMPT}, {DARK_NEGATIVE}" if is_dark else NEGATIVE_PROMPT
                full_prompt = f"{CHARACTER_PREFIX}, {scene_prompt}{suffix}"
                _hand_triggers = {"hand", "finger", "wave", "hold", "keyboard", "typing", "fanning", "umbrella", "mug", "drink"}
                if any(t in full_prompt.lower() for t in _hand_triggers):
                    full_prompt += ", delicate silver ring on her finger, (five fingers:1.5)"

                prompt_embeds = cls._compel(full_prompt)
                negative_embeds = cls._compel(negative)
                [prompt_embeds, negative_embeds] = cls._compel.pad_conditioning_tensors_to_same_length([prompt_embeds, negative_embeds])

                image = pipe(
                    prompt_embeds=prompt_embeds,
                    negative_prompt_embeds=negative_embeds,
                    num_inference_steps=20,
                    guidance_scale=6.5,
                    clip_skip=2,
                    generator=generator,
                    width=_REFINE_SIZE,
                    height=_REFINE_SIZE,
                ).images[0]

            print(f"[ImageGen] HQ generation complete for '{state}', saving at {_FINAL_SIZE}px...")
            cls._save_hq(state, image)

        except Exception as e:
            print(f"[ImageGen] HQ generation failed for '{state}': {e} — falling back to upscale only.")
            cls._upscale(state, src)

    @classmethod
    def _save_hq(cls, state: str, img):
        """Resize to _FINAL_SIZE with LANCZOS and save atomically as the HQ file."""
        from PIL import Image
        if img.width != _FINAL_SIZE or img.height != _FINAL_SIZE:
            img = img.resize((_FINAL_SIZE, _FINAL_SIZE), Image.LANCZOS)
        dst = cls._hq_path(state)
        tmp = dst.with_suffix('.tmp.png')
        img.save(tmp)
        tmp.rename(dst)
        print(f"[ImageGen] HQ saved: {dst.name} ({dst.stat().st_size // 1024}KB)")

    @classmethod
    def _upscale(cls, state: str, src: Path):
        """Upscale-only path for existing images with no stored prompt (Real-ESRGAN)."""
        from PIL import Image
        img = Image.open(src).convert('RGB')
        cls._upscale_image(state, img)

    @classmethod
    def _upscale_image(cls, state: str, img):
        """Run Real-ESRGAN on a PIL image and save as the HQ file."""
        model = cls._get_upscaler()
        if model is None:
            return
        try:
            import numpy as np
            tensor = torch.from_numpy(
                np.array(img).astype('float32') / 255.0
            ).permute(2, 0, 1).unsqueeze(0)

            with torch.no_grad():
                output = model(tensor).squeeze(0).permute(1, 2, 0).clamp(0, 1)

            from PIL import Image
            result = Image.fromarray((output.numpy() * 255).astype('uint8'))
            if result.width > _FINAL_SIZE or result.height > _FINAL_SIZE:
                result = result.resize((_FINAL_SIZE, _FINAL_SIZE), Image.LANCZOS)
            dst = cls._hq_path(state)
            tmp = dst.with_suffix('.tmp.png')
            result.save(tmp)
            tmp.rename(dst)
            print(f"[ImageGen] HQ saved: {dst.name} ({dst.stat().st_size // 1024}KB)")
        except Exception as e:
            print(f"[ImageGen] Upscale failed for '{state}': {e}")

    @classmethod
    def _get_upscaler(cls) -> _RRDBNet | None:
        if cls._upscaler is not None:
            return cls._upscaler

        _REALESRGAN_WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not _REALESRGAN_WEIGHTS_PATH.exists():
            print("[ImageGen] Downloading RealESRGAN anime weights (~17MB)...")
            import urllib.request
            try:
                urllib.request.urlretrieve(_REALESRGAN_WEIGHTS_URL, _REALESRGAN_WEIGHTS_PATH)
                print("[ImageGen] RealESRGAN weights downloaded.")
            except Exception as e:
                print(f"[ImageGen] Failed to download weights: {e}")
                return None

        model = _RRDBNet(num_in_ch=3, num_out_ch=3, scale=4, num_feat=64, num_block=6, num_grow_ch=32)
        state_dict = torch.load(_REALESRGAN_WEIGHTS_PATH, map_location='cpu', weights_only=True)
        state_dict = state_dict.get('params_ema') or state_dict.get('params') or state_dict
        model.load_state_dict(state_dict, strict=True)
        model.eval()
        cls._upscaler = model
        print("[ImageGen] RealESRGAN upscaler ready.")
        return cls._upscaler
