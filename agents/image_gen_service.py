import json
import os
import random
import signal
import threading
import time
import warnings
from collections import deque
from contextlib import contextmanager
from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F
from compel import Compel, DiffusersTextualInversionManager
from diffusers import StableDiffusionPipeline, DPMSolverMultistepScheduler, AutoencoderKL

from agents.persona_states import CHARACTER_PREFIX

MODEL_ID    = "Lykon/dreamshaper-8"
VAE_ID      = "stabilityai/sd-vae-ft-mse"
FIXED_SEED  = 42
OUTPUT_DIR  = Path("tmp/persona")
HQ_QUEUE_DIR       = Path("env/hq_queue")
UHQ_QUEUE_DIR      = Path("env/uhq_queue")
PRIORITY_QUEUE_DIR = Path("env/priority_queue")
GPU_LOCK_PATH      = Path("env/gpu.lock")

PROMPT_SUFFIX      = ", soft lighting, clean background"
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

# Final output size for HQ images
_FINAL_SIZE = 1024

# Real-ESRGAN weights (fallback upscaler for legacy images with no stored prompt)
_REALESRGAN_WEIGHTS_URL  = "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth"
_REALESRGAN_WEIGHTS_PATH = Path("env/weights/RealESRGAN_x4plus_anime_6B.pth")

# Textual Inversion negative embeddings — cached to env/weights/ti/ on first load.
# NOTE: verify these HuggingFace repo IDs before first run; community repos occasionally move.
TI_WEIGHTS_DIR = Path("env/weights/ti")
TI_EMBEDDINGS: list[tuple[str, str, str]] = [
    # (hf_repo_id,  filename,  token)
    # Add embeddings here to enable them. They are downloaded to TI_WEIGHTS_DIR on first load
    # and prepended to the negative prompt. Leave empty to disable.
    # Example: ("yesyeahvh/bad-hands-5", "bad-hands-5.pt", "bad-hands-5")
]

warnings.filterwarnings("ignore", message="Token indices sequence length is longer than")
warnings.filterwarnings("ignore", message="Pipelines loaded with `dtype=torch.float16`")

DEVICE     = "cuda" if torch.cuda.is_available() else "cpu"
TORCH_DTYPE = torch.float16 if DEVICE == "cuda" else torch.float32

if DEVICE == "cpu":
    print("WARNING: CUDA is not available. Image generation will run on CPU and be very slow.")


@contextmanager
def gpu_lock():
    """Cross-process GPU lock shared between Flask (SD 1.5) and hq_gen_worker (SDXL)."""
    import sys
    GPU_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(GPU_LOCK_PATH, 'w') as f:
        if sys.platform == 'win32':
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)


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


def _load_textual_inversions(pipeline) -> str:
    """Download (if needed) and load TI embeddings into a pipeline.

    Returns a comma-separated prefix string of loaded token names for
    prepending to the negative prompt, e.g. 'EasyNegative, bad-hands-5, '.
    Failures are non-fatal — skipped embeddings are simply omitted.
    """
    TI_WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    loaded = []
    for repo_id, filename, token in TI_EMBEDDINGS:
        dest = TI_WEIGHTS_DIR / filename
        if not dest.exists():
            print(f"[ImageGen] Downloading TI embedding '{filename}' from {repo_id}...")
            try:
                from huggingface_hub import hf_hub_download
                hf_hub_download(repo_id=repo_id, filename=filename, local_dir=str(TI_WEIGHTS_DIR))
            except Exception as e:
                print(f"[ImageGen] Could not download '{filename}': {e}")
                continue
        try:
            pipeline.load_textual_inversion(str(dest), token=token)
            loaded.append(token)
            print(f"[ImageGen] TI embedding loaded: '{token}'")
        except Exception as e:
            print(f"[ImageGen] Could not load '{token}': {e}")
    return ", ".join(loaded) + ", " if loaded else ""


class ImageGenService:
    _pipeline: StableDiffusionPipeline | None = None
    _compel: Compel | None = None
    _ti_negative_prefix: str = ""
    _lock = threading.Lock()
    _in_progress: set[str] = set()

    # Startup upscale scheduler (legacy images with no stored prompt → Real-ESRGAN)
    _upscaler: _RRDBNet | None = None
    _upgrade_queue: deque = deque()
    _upgrade_in_progress: set[str] = set()
    _upgrade_scheduler_started: bool = False

    # ------------------------------------------------------------------ #
    #  Standard generation (SD 1.5, 512×512, fast)                       #
    # ------------------------------------------------------------------ #

    @classmethod
    def _get_pipeline(cls) -> StableDiffusionPipeline:
        if cls._pipeline is None:
            print(f"[ImageGen] Loading pipeline on {DEVICE}...")
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
            cls._ti_negative_prefix = _load_textual_inversions(cls._pipeline)
            cls._compel = Compel(
                tokenizer=cls._pipeline.tokenizer,
                text_encoder=cls._pipeline.text_encoder,
                textual_inversion_manager=DiffusersTextualInversionManager(cls._pipeline),
                truncate_long_prompts=False,
            )
        return cls._pipeline

    @classmethod
    def get_cached(cls, state: str) -> Path | None:
        """Return best available cached image: UHQ > HQ > standard."""
        uhq = cls._uhq_path(state)
        if uhq.exists():
            return uhq
        hq = cls._hq_path(state)
        if hq.exists():
            return hq
        std = OUTPUT_DIR / f"{state}.png"
        return std if std.exists() else None

    @classmethod
    def generate(cls, state: str, scene_prompt: str, seed: int | None = None) -> Path:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = OUTPUT_DIR / f"{state}.png"

        if output_path.exists():
            return output_path

        # Yield GPU from the worker so the fast image can generate immediately
        cls._write_priority(state)
        cls._signal_worker()

        effective_seed = seed if seed is not None else FIXED_SEED

        cls._in_progress.add(state)
        try:
            with cls._lock, gpu_lock():
                pipe = cls._get_pipeline()
                pipe.to(DEVICE)
                torch.cuda.empty_cache()
                try:
                    generator = torch.Generator(DEVICE).manual_seed(effective_seed)
                    is_dark = state.endswith("_dark")
                    suffix = PROMPT_SUFFIX_DARK if is_dark else PROMPT_SUFFIX
                    negative = f"{cls._ti_negative_prefix}{NEGATIVE_PROMPT}, {DARK_NEGATIVE}" if is_dark else f"{cls._ti_negative_prefix}{NEGATIVE_PROMPT}"
                    full_prompt = f"{CHARACTER_PREFIX}, {scene_prompt}{suffix}"
                    _hand_triggers = {"hand", "finger", "wave", "hold", "keyboard", "typing", "fanning", "umbrella", "mug", "drink"}
                    if any(t in full_prompt.lower() for t in _hand_triggers):
                        full_prompt += ", delicate silver ring on her finger, (five fingers:1.5)"
                    print(f"[ImageGen] Generating '{state}'" + (" (dark)" if is_dark else ""))
                    print(f"[ImageGen] Prompt: {full_prompt}")
                    prompt_embeds = cls._compel(full_prompt)
                    negative_embeds = cls._compel(negative)
                    [prompt_embeds, negative_embeds] = cls._compel.pad_conditioning_tensors_to_same_length(
                        [prompt_embeds, negative_embeds]
                    )
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
                    from PIL.PngImagePlugin import PngInfo
                    info = PngInfo()
                    info.add_text("scene_prompt", scene_prompt)
                    info.add_text("tier", "fast")
                    info.add_text("seed", str(effective_seed))
                    image.save(output_path, pnginfo=info)
                finally:
                    # Release VRAM so the HQ worker can use it
                    pipe.to('cpu')
                    torch.cuda.empty_cache()
        finally:
            cls._in_progress.discard(state)
            cls._clear_priority(state)

        # Queue HQ job for the worker process
        if not cls._hq_path(state).exists():
            cls._queue_hq(state, scene_prompt, seed=effective_seed)

        return output_path

    # ------------------------------------------------------------------ #
    #  HQ queue — jobs consumed by hq_gen_worker.py                      #
    # ------------------------------------------------------------------ #

    @classmethod
    def _queue_hq(cls, state: str, scene_prompt: str, seed: int | None = None,
                  output_stem: str | None = None, force: bool = False):
        HQ_QUEUE_DIR.mkdir(parents=True, exist_ok=True)
        job_stem = output_stem if output_stem else state
        job_file = HQ_QUEUE_DIR / f"{job_stem}.json"
        if not force and job_file.exists():
            return
        data: dict = {"scene_prompt": scene_prompt}
        if seed is not None:
            data["seed"] = seed
        if output_stem is not None:
            data["output_stem"] = output_stem
            data["state"] = state  # original state for dark-mode detection in worker
        job_file.write_text(json.dumps(data))
        print(f"[ImageGen] HQ job queued: {job_stem}")

    # ------------------------------------------------------------------ #
    #  HQ paths + save (shared with hq_gen_worker.py)                    #
    # ------------------------------------------------------------------ #

    @classmethod
    def _hq_path(cls, state: str) -> Path:
        return OUTPUT_DIR / f"{state}_hq.png"

    @classmethod
    def _save_hq(cls, state: str, img, scene_prompt: str = ""):
        """Resize to _FINAL_SIZE with LANCZOS and save atomically as the HQ file."""
        from PIL import Image
        from PIL.PngImagePlugin import PngInfo
        if img.width != _FINAL_SIZE or img.height != _FINAL_SIZE:
            img = img.resize((_FINAL_SIZE, _FINAL_SIZE), Image.LANCZOS)
        info = PngInfo()
        if scene_prompt:
            info.add_text("scene_prompt", scene_prompt)
            info.add_text("tier", "mq")
        dst = cls._hq_path(state)
        tmp = dst.with_suffix('.tmp.png')
        img.save(tmp, pnginfo=info)
        tmp.rename(dst)
        print(f"[ImageGen] HQ saved: {dst.name} ({dst.stat().st_size // 1024}KB)")

    # ------------------------------------------------------------------ #
    #  UHQ paths + save + queue                                           #
    # ------------------------------------------------------------------ #

    @classmethod
    def _uhq_path(cls, state: str) -> Path:
        return OUTPUT_DIR / f"{state}_uhq.png"

    @classmethod
    def _save_uhq(cls, state: str, img, scene_prompt: str = ""):
        """Save atomically as the UHQ file with embedded metadata."""
        from PIL.PngImagePlugin import PngInfo
        info = PngInfo()
        if scene_prompt:
            info.add_text("scene_prompt", scene_prompt)
            info.add_text("tier", "uhq")
        dst = cls._uhq_path(state)
        tmp = dst.with_suffix('.tmp.png')
        img.save(tmp, pnginfo=info)
        tmp.rename(dst)
        print(f"[ImageGen] UHQ saved: {dst.name} ({dst.stat().st_size // 1024}KB)")

    @classmethod
    def _queue_uhq(cls, state: str, scene_prompt: str, seed: int | None = None,
                   output_stem: str | None = None, force: bool = False):
        UHQ_QUEUE_DIR.mkdir(parents=True, exist_ok=True)
        job_stem = output_stem if output_stem else state
        job_file = UHQ_QUEUE_DIR / f"{job_stem}.json"
        if not force and job_file.exists():
            return
        data: dict = {"scene_prompt": scene_prompt}
        if seed is not None:
            data["seed"] = seed
        if output_stem is not None:
            data["output_stem"] = output_stem
            data["state"] = state  # original state for dark-mode detection in worker
        job_file.write_text(json.dumps(data))
        print(f"[ImageGen] UHQ job queued: {job_stem}")

    # ------------------------------------------------------------------ #
    #  Priority queue + worker interrupt                                  #
    # ------------------------------------------------------------------ #

    @classmethod
    def _write_priority(cls, state: str):
        PRIORITY_QUEUE_DIR.mkdir(parents=True, exist_ok=True)
        pf = PRIORITY_QUEUE_DIR / f"{state}.json"
        if not pf.exists():
            pf.write_text(json.dumps({"state": state}))

    @classmethod
    def _clear_priority(cls, state: str):
        (PRIORITY_QUEUE_DIR / f"{state}.json").unlink(missing_ok=True)
        remaining = list(PRIORITY_QUEUE_DIR.glob("*.json")) if PRIORITY_QUEUE_DIR.exists() else []
        if not remaining:
            cls._start_hq_worker()

    @classmethod
    def _signal_worker(cls):
        """Send SIGTERM to the HQ worker — GPU kernel dies, flock released immediately.

        The PID file is deleted here so _start_hq_worker() knows to spawn a fresh
        process after generation — the old PID stays openable (zombie/handle) on both
        Linux and Windows, which would fool the alive check otherwise.
        """
        pid_file = Path("env/hq_worker.pid")
        if not pid_file.exists():
            return
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            print(f"[ImageGen] Sent SIGTERM to worker (PID {pid}) — priority request.")
        except (ProcessLookupError, ValueError, OSError):
            pass
        finally:
            pid_file.unlink(missing_ok=True)

    # ------------------------------------------------------------------ #
    #  PNG tEXt metadata                                                  #
    # ------------------------------------------------------------------ #

    @classmethod
    def _read_meta(cls, state: str) -> dict | None:
        """Read scene_prompt and tier from the fast PNG's tEXt chunks."""
        path = OUTPUT_DIR / f"{state}.png"
        if not path.exists():
            return None
        try:
            from PIL import Image
            with Image.open(path) as img:
                text = getattr(img, 'text', {})
                return text if text.get('scene_prompt') else None
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    #  Experiment generation (admin one-shot)                             #
    #  fast = synchronous; mq/uhq = queued to hq_gen_worker              #
    # ------------------------------------------------------------------ #

    @classmethod
    def generate_experiment(cls, state: str, scene_prompt: str,
                            seed: int | None = None, tier: str = 'fast') -> Path | None:
        """Generate an experiment image to {state}_{tier}_exp.png.

        Never touches the canonical state image or its HQ/UHQ counterparts.
        fast tier: runs synchronously, returns Path when done.
        mq/uhq tiers: queued to hq_gen_worker, returns None (caller should poll).
        """
        if tier not in ('fast', 'mq', 'uhq'):
            raise ValueError(f"Invalid tier '{tier}'")

        effective_seed = seed if seed is not None else random.randint(1, 2 ** 31 - 1)
        output_stem = f"{state}_{tier}_exp"

        if tier == 'mq':
            (OUTPUT_DIR / f"{output_stem}.png").unlink(missing_ok=True)
            cls._queue_hq(state, scene_prompt, seed=effective_seed,
                          output_stem=output_stem, force=True)
            return None
        if tier == 'uhq':
            (OUTPUT_DIR / f"{output_stem}.png").unlink(missing_ok=True)
            cls._queue_uhq(state, scene_prompt, seed=effective_seed,
                           output_stem=output_stem, force=True)
            return None

        # fast — synchronous
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = OUTPUT_DIR / f"{output_stem}.png"

        cls._write_priority(state)
        cls._signal_worker()

        try:
            with cls._lock, gpu_lock():
                pipe = cls._get_pipeline()
                pipe.to(DEVICE)
                torch.cuda.empty_cache()
                try:
                    generator = torch.Generator(DEVICE).manual_seed(effective_seed)
                    is_dark = state.endswith("_dark")
                    suffix = PROMPT_SUFFIX_DARK if is_dark else PROMPT_SUFFIX
                    negative = f"{cls._ti_negative_prefix}{NEGATIVE_PROMPT}, {DARK_NEGATIVE}" if is_dark else f"{cls._ti_negative_prefix}{NEGATIVE_PROMPT}"
                    full_prompt = f"{CHARACTER_PREFIX}, {scene_prompt}{suffix}"
                    _hand_triggers = {"hand", "finger", "wave", "hold", "keyboard",
                                      "typing", "fanning", "umbrella", "mug", "drink"}
                    if any(t in full_prompt.lower() for t in _hand_triggers):
                        full_prompt += ", delicate silver ring on her finger, (five fingers:1.5)"
                    print(f"[ImageGen] Experiment '{state}' tier=fast seed={effective_seed}")
                    print(f"[ImageGen] Prompt: {full_prompt}")
                    prompt_embeds = cls._compel(full_prompt)
                    negative_embeds = cls._compel(negative)
                    [prompt_embeds, negative_embeds] = cls._compel.pad_conditioning_tensors_to_same_length(
                        [prompt_embeds, negative_embeds]
                    )
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
                    from PIL.PngImagePlugin import PngInfo
                    info = PngInfo()
                    info.add_text("scene_prompt", scene_prompt)
                    info.add_text("tier", "experiment_fast")
                    info.add_text("seed", str(effective_seed))
                    image.save(output_path, pnginfo=info)
                finally:
                    pipe.to('cpu')
                    torch.cuda.empty_cache()
        finally:
            cls._clear_priority(state)

        return output_path

    @classmethod
    def _read_meta_path(cls, path: Path) -> dict | None:
        """Read scene_prompt and tier from tEXt chunks of an arbitrary PNG path."""
        if not path.exists():
            return None
        try:
            from PIL import Image
            with Image.open(path) as img:
                text = getattr(img, 'text', {})
                return text if text.get('scene_prompt') else None
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    #  Requeue API                                                         #
    # ------------------------------------------------------------------ #

    @classmethod
    def requeue(cls, state: str, tier: str = 'mq') -> bool:
        """Re-queue a tier for regeneration using the same seed (retry, not re-roll)."""
        meta = cls._read_meta(state)
        if not meta:
            return False
        scene_prompt = meta['scene_prompt']
        seed = int(meta['seed']) if meta.get('seed') else None
        if tier == 'mq':
            cls._hq_path(state).unlink(missing_ok=True)
            cls._queue_hq(state, scene_prompt, seed=seed)
        elif tier == 'uhq':
            cls._uhq_path(state).unlink(missing_ok=True)
            cls._queue_uhq(state, scene_prompt, seed=seed)
        return True

    @classmethod
    def invalidate(cls, state: str, tier: str = 'all') -> tuple[Path | None, int]:
        """Re-roll a tier with a fresh random seed (always produces a different result).

        Each tier is invalidated independently — other tiers are untouched.

        tier='fast': delete fast PNG, regenerate synchronously with new seed.
        tier='mq':   delete MQ PNG, re-queue MQ with new seed.
        tier='uhq':  delete UHQ PNG, re-queue UHQ with new seed.
        tier='all':  delete all three, regenerate fast with new seed, queue MQ.

        Returns (path, new_seed) — path is None for worker-queued tiers.
        """
        meta = cls._read_meta(state)
        if not meta:
            raise ValueError(f"No metadata for state '{state}'")
        scene_prompt = meta['scene_prompt']
        new_seed = random.randint(1, 2 ** 31 - 1)

        if tier == 'fast':
            (OUTPUT_DIR / f"{state}.png").unlink(missing_ok=True)
            path = cls.generate(state, scene_prompt, seed=new_seed)
            return path, new_seed
        elif tier == 'mq':
            cls._hq_path(state).unlink(missing_ok=True)
            cls._queue_hq(state, scene_prompt, seed=new_seed)
            return None, new_seed
        elif tier == 'uhq':
            cls._uhq_path(state).unlink(missing_ok=True)
            cls._queue_uhq(state, scene_prompt, seed=new_seed)
            return None, new_seed
        elif tier == 'all':
            for path in [OUTPUT_DIR / f"{state}.png", cls._hq_path(state), cls._uhq_path(state)]:
                path.unlink(missing_ok=True)
            (HQ_QUEUE_DIR / f"{state}.json").unlink(missing_ok=True)
            (UHQ_QUEUE_DIR / f"{state}.json").unlink(missing_ok=True)
            path = cls.generate(state, scene_prompt, seed=new_seed)
            return path, new_seed
        else:
            raise ValueError(f"Unknown tier '{tier}'")

    # ------------------------------------------------------------------ #
    #  Startup upscale scheduler (legacy images — Real-ESRGAN fallback)  #
    # ------------------------------------------------------------------ #

    @classmethod
    def start_upgrade_scheduler(cls):
        if cls._upgrade_scheduler_started:
            return
        cls._upgrade_scheduler_started = True

        prompt_queued = 0
        legacy_queued = 0
        if OUTPUT_DIR.exists():
            for p in OUTPUT_DIR.glob("*.png"):
                stem = p.stem
                if stem.endswith("_hq") or stem.endswith("_uhq") or stem.endswith("_exp"):
                    continue
                if cls._hq_path(stem).exists() or (HQ_QUEUE_DIR / f"{stem}.json").exists():
                    continue
                meta = cls._read_meta(stem)
                if meta:
                    cls._queue_hq(stem, meta['scene_prompt'])
                    prompt_queued += 1
                else:
                    cls._upgrade_queue.append(stem)
                    legacy_queued += 1

        if prompt_queued or legacy_queued:
            print(f"[ImageGen] Upscale scheduler started — {prompt_queued} prompt-based, {legacy_queued} legacy (Real-ESRGAN).")
        else:
            print("[ImageGen] Upscale scheduler started.")

        threading.Thread(target=cls._upgrade_loop, daemon=True).start()
        cls._start_hq_worker()

    @classmethod
    def _start_hq_worker(cls):
        """Start hq_gen_worker.py as a detached subprocess. Guarded by a PID file."""
        import os
        import subprocess
        import sys

        pid_file = Path("env/hq_worker.pid")
        pid_file.parent.mkdir(parents=True, exist_ok=True)

        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                if sys.platform == 'win32':
                    import ctypes
                    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
                    alive = bool(handle)
                    if handle:
                        ctypes.windll.kernel32.CloseHandle(handle)
                else:
                    try:
                        os.kill(pid, 0)
                        alive = True
                    except ProcessLookupError:
                        alive = False
                if alive:
                    print(f"[ImageGen] HQ worker already running (PID {pid}), skipping.")
                    return
            except ValueError:
                pass  # stale/corrupt PID file

        worker = Path(__file__).parent / "hq_gen_worker.py"
        proc = subprocess.Popen(
            [sys.executable, str(worker)],
        )
        pid_file.write_text(str(proc.pid))
        print(f"[ImageGen] HQ worker started (PID {proc.pid}).")

        import atexit
        import signal

        def _shutdown_worker():
            try:
                os.kill(proc.pid, signal.SIGTERM)
                print(f"[ImageGen] HQ worker (PID {proc.pid}) terminated.")
            except (ProcessLookupError, OSError):
                pass
            pid_file.unlink(missing_ok=True)
            GPU_LOCK_PATH.unlink(missing_ok=True)

        atexit.register(_shutdown_worker)

    @classmethod
    def _upgrade_loop(cls):
        while True:
            if cls._upgrade_queue and not cls._in_progress:
                state = cls._upgrade_queue.popleft()
                if state in cls._upgrade_in_progress or cls._hq_path(state).exists():
                    continue
                std_path = OUTPUT_DIR / f"{state}.png"
                if not std_path.exists():
                    continue
                cls._upgrade_in_progress.add(state)
                try:
                    cls._upscale(state, std_path)
                finally:
                    cls._upgrade_in_progress.discard(state)
            else:
                time.sleep(5)

    @classmethod
    def _upscale(cls, state: str, src: Path):
        """Real-ESRGAN upscale for legacy images with no stored prompt."""
        from PIL import Image
        img = Image.open(src).convert('RGB')
        cls._upscale_image(state, img)

    @classmethod
    def _upscale_image(cls, state: str, img):
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
