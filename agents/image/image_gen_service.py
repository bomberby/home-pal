import gc
import json
import random
import threading
import time
import warnings
from collections import deque
from pathlib import Path

warnings.filterwarnings("ignore", message="Token indices sequence length is longer than")
warnings.filterwarnings("ignore", message="Pipelines loaded with `dtype=torch.float16`")

import torch
from compel import Compel, DiffusersTextualInversionManager
from diffusers import StableDiffusionPipeline, DPMSolverMultistepScheduler, AutoencoderKL
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from agents.image.gpu_lock import (
    claim_gpu as _claim_gpu_impl,
    cleanup_stale_lock, cleanup_stale_priority,
)
from agents.image.hq_worker_manager import WORKER_BOOT_PATH, start_hq_worker
from agents.image.image_prompt import build_full_prompt, build_negative_prompt, _load_textual_inversions
from agents.image.realesrgan_upscaler import FINAL_SIZE as _FINAL_SIZE
from agents.image.realesrgan_upscaler import upscale as _realesrgan_upscale

MODEL_ID    = "Lykon/dreamshaper-8"
VAE_ID      = "stabilityai/sd-vae-ft-mse"
FIXED_SEED  = 42
OUTPUT_DIR  = Path("tmp/persona")
HQ_QUEUE_DIR          = Path("env/hq_queue")
UHQ_QUEUE_DIR         = Path("env/uhq_queue")

DEVICE     = "cuda" if torch.cuda.is_available() else "cpu"
TORCH_DTYPE = torch.float16 if DEVICE == "cuda" else torch.float32

PIPELINE_EVICT_IDLE = 60.0  # seconds idle before unloading pipeline from RAM

if DEVICE == "cpu":
    print("WARNING: CUDA is not available. Image generation will run on CPU and be very slow.")


def _write_job_file(queue_dir: Path, label: str, state: str, scene_prompt: str,
                    seed: int | None, output_stem: str | None, force: bool) -> None:
    queue_dir.mkdir(parents=True, exist_ok=True)
    job_stem = output_stem or state
    job_file = queue_dir / f"{job_stem}.json"
    if not force and job_file.exists():
        return
    data: dict = {"scene_prompt": scene_prompt}
    if seed is not None:
        data["seed"] = seed
    if output_stem is not None:
        data["output_stem"] = output_stem
        data["state"] = state
    job_file.write_text(json.dumps(data))
    print(f"[ImageGen] {label} job queued: {job_stem}")


def _atomic_save(img, dst: Path, scene_prompt: str, tier: str) -> None:
    info = PngInfo()
    if scene_prompt:
        info.add_text("scene_prompt", scene_prompt)
        info.add_text("tier", tier)
    tmp = dst.with_suffix('.tmp.png')
    img.save(tmp, pnginfo=info)
    tmp.rename(dst)
    print(f"[ImageGen] {tier.upper()} saved: {dst.name} ({dst.stat().st_size // 1024}KB)")


class ImageGenService:
    _pipeline: StableDiffusionPipeline | None = None
    _compel: Compel | None = None
    _ti_negative_prefix: str = ""
    _lock = threading.Lock()
    _in_progress: set[str] = set()

    _evict_timer: threading.Timer | None = None
    _evict_lock = threading.Lock()  # protects _evict_timer only; never held alongside _lock

    # Startup upscale scheduler (legacy images with no stored prompt → Real-ESRGAN)
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
    def _cancel_eviction(cls) -> None:
        with cls._evict_lock:
            if cls._evict_timer is not None:
                cls._evict_timer.cancel()
                cls._evict_timer = None

    @classmethod
    def _schedule_eviction(cls) -> None:
        with cls._evict_lock:
            if cls._evict_timer is not None:
                cls._evict_timer.cancel()
            cls._evict_timer = threading.Timer(PIPELINE_EVICT_IDLE, cls._evict_pipeline)
            cls._evict_timer.daemon = True
            cls._evict_timer.start()

    @classmethod
    def _evict_pipeline(cls) -> None:
        with cls._lock:
            if cls._pipeline is not None:
                cls._pipeline = None
                cls._compel = None
                gc.collect()
                torch.cuda.empty_cache()
                print(f"[ImageGen] Pipeline evicted from RAM after {PIPELINE_EVICT_IDLE:.0f}s idle.")
        with cls._evict_lock:
            cls._evict_timer = None

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
    def _run_fast(cls, state: str, scene_prompt: str, seed: int,
                  output_path: Path, tier: str = "fast") -> None:
        """Synchronously generate a 512×512 image and save to output_path.

        Acquires both the threading lock (prevents concurrent Flask generations)
        and the cross-process GPU lock (yields to this call over the HQ worker).
        Moves the pipeline to CPU and clears VRAM cache on exit.
        """
        cls._cancel_eviction()
        with cls._lock, _claim_gpu_impl(key=state, on_worker_killed=start_hq_worker):
            pipe = cls._get_pipeline()
            pipe.to(DEVICE)
            torch.cuda.empty_cache()
            try:
                generator = torch.Generator(DEVICE).manual_seed(seed)
                full_prompt = build_full_prompt(scene_prompt, state)
                negative = build_negative_prompt(state, cls._ti_negative_prefix)
                print(f"[ImageGen] Generating '{state}' tier={tier} seed={seed}" +
                      (" (dark)" if state.endswith("_dark") else ""))
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
                info = PngInfo()
                info.add_text("scene_prompt", scene_prompt)
                info.add_text("tier", tier)
                info.add_text("seed", str(seed))
                image.save(output_path, pnginfo=info)
            finally:
                # Release VRAM so the HQ worker can use it
                pipe.to('cpu')
                torch.cuda.empty_cache()
        cls._schedule_eviction()

    @classmethod
    def generate(cls, state: str, scene_prompt: str, seed: int | None = None) -> Path:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = OUTPUT_DIR / f"{state}.png"

        if output_path.exists():
            return output_path

        effective_seed = seed if seed is not None else FIXED_SEED

        cls._in_progress.add(state)
        try:
            cls._run_fast(state, scene_prompt, effective_seed, output_path)
        finally:
            cls._in_progress.discard(state)

        if not cls._hq_path(state).exists():
            cls._queue_hq(state, scene_prompt, seed=effective_seed)

        return output_path

    # ------------------------------------------------------------------ #
    #  HQ queue — jobs consumed by hq_gen_worker.py                      #
    # ------------------------------------------------------------------ #

    @classmethod
    def _queue_hq(cls, state: str, scene_prompt: str, seed: int | None = None,
                  output_stem: str | None = None, force: bool = False):
        _write_job_file(HQ_QUEUE_DIR, "HQ", state, scene_prompt, seed, output_stem, force)

    # ------------------------------------------------------------------ #
    #  HQ paths + save (shared with hq_gen_worker.py)                    #
    # ------------------------------------------------------------------ #

    @classmethod
    def _hq_path(cls, state: str) -> Path:
        return OUTPUT_DIR / f"{state}_hq.png"

    @classmethod
    def _save_hq(cls, state: str, img, scene_prompt: str = ""):
        if img.width != _FINAL_SIZE or img.height != _FINAL_SIZE:
            img = img.resize((_FINAL_SIZE, _FINAL_SIZE), Image.LANCZOS)
        _atomic_save(img, cls._hq_path(state), scene_prompt, "mq")

    # ------------------------------------------------------------------ #
    #  UHQ paths + save + queue                                           #
    # ------------------------------------------------------------------ #

    @classmethod
    def _uhq_path(cls, state: str) -> Path:
        return OUTPUT_DIR / f"{state}_uhq.png"

    @classmethod
    def _save_uhq(cls, state: str, img, scene_prompt: str = ""):
        _atomic_save(img, cls._uhq_path(state), scene_prompt, "uhq")

    @classmethod
    def _queue_uhq(cls, state: str, scene_prompt: str, seed: int | None = None,
                   output_stem: str | None = None, force: bool = False):
        _write_job_file(UHQ_QUEUE_DIR, "UHQ", state, scene_prompt, seed, output_stem, force)

    # ------------------------------------------------------------------ #
    #  GPU preemption                                                     #
    # ------------------------------------------------------------------ #

    @classmethod
    def claim_gpu(cls):
        return _claim_gpu_impl(
            skip_if=lambda: bool(cls._in_progress),
            on_worker_killed=start_hq_worker,
        )

    # ------------------------------------------------------------------ #
    #  PNG tEXt metadata                                                  #
    # ------------------------------------------------------------------ #

    @classmethod
    def _read_meta(cls, state: str) -> dict | None:
        """Read scene_prompt from the best available PNG's tEXt chunks.

        Falls back to HQ then UHQ when the fast image doesn't exist, so
        states that were only generated at higher tiers can still participate
        in re-roll and experiment workflows.
        """
        for path in [OUTPUT_DIR / f"{state}.png", cls._hq_path(state), cls._uhq_path(state)]:
            meta = cls._read_meta_path(path)
            if meta:
                return meta
        return None

    @classmethod
    def _read_meta_path(cls, path: Path) -> dict | None:
        """Read scene_prompt and tier from tEXt chunks of an arbitrary PNG path."""
        if not path.exists():
            return None
        try:
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
        cls._run_fast(state, scene_prompt, effective_seed, output_path, tier="experiment_fast")
        return output_path

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
        """Re-roll a tier with a fresh random seed. Returns (path, new_seed) — path is None for worker-queued tiers."""
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
        cleanup_stale_lock()
        cleanup_stale_priority()
        WORKER_BOOT_PATH.unlink(missing_ok=True)

        prompt_queued = 0
        legacy_queued = 0
        if OUTPUT_DIR.exists():
            for tmp in OUTPUT_DIR.glob("*.tmp.png"):
                tmp.unlink(missing_ok=True)
                print(f"[ImageGen] Cleaned up stale tmp file: {tmp.name}")
            for p in OUTPUT_DIR.glob("*.png"):
                stem = p.stem
                if '.tmp' in stem:
                    continue
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
        start_hq_worker()

    @classmethod
    def _upgrade_loop(cls):
        _last_worker_check = 0.0
        while True:
            now = time.time()
            if now - _last_worker_check >= 60:
                start_hq_worker()
                _last_worker_check = now

            if cls._upgrade_queue and not cls._in_progress:
                state = cls._upgrade_queue.popleft()
                if state in cls._upgrade_in_progress or cls._hq_path(state).exists():
                    continue
                std_path = OUTPUT_DIR / f"{state}.png"
                if not std_path.exists():
                    continue
                cls._upgrade_in_progress.add(state)
                try:
                    _realesrgan_upscale(std_path, cls._hq_path(state))
                finally:
                    cls._upgrade_in_progress.discard(state)
            else:
                time.sleep(5)
