import json
import os
import random
import signal
import sys
import threading
import time
import warnings
from collections import deque
from contextlib import contextmanager
from pathlib import Path

import torch
from compel import Compel, DiffusersTextualInversionManager
from diffusers import StableDiffusionPipeline, DPMSolverMultistepScheduler, AutoencoderKL

from agents.gpu_lock import GPU_LOCK_PATH, WORKER_PID_PATH, gpu_lock
from agents.persona.states import CHARACTER_PREFIX
from agents.realesrgan_upscaler import FINAL_SIZE as _FINAL_SIZE
from agents.realesrgan_upscaler import upscale as _realesrgan_upscale

MODEL_ID    = "Lykon/dreamshaper-8"
VAE_ID      = "stabilityai/sd-vae-ft-mse"
FIXED_SEED  = 42
OUTPUT_DIR  = Path("tmp/persona")
HQ_QUEUE_DIR       = Path("env/hq_queue")
UHQ_QUEUE_DIR      = Path("env/uhq_queue")
PRIORITY_QUEUE_DIR = Path("env/priority_queue")
WORKER_BOOT_PATH      = Path("env/hq_worker.booting")
WORKER_HEARTBEAT_PATH = Path("env/hq_worker.heartbeat")

_WORKER_HEARTBEAT_TTL = 90.0  # seconds — 3× the worker's 30s heartbeat write interval

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
    "(too few fingers:1.8), (missing finger:1.8), "
    "(segmented fingers:1.6), (jointed fingers:1.6), (broken fingers:1.6), (disconnected finger:1.6), "
    "(deformed face:1.4), (disfigured face:1.4), (malformed face:1.3), (bad face:1.3), "
    "(cross-eyed:1.2), (asymmetric eyes:1.2), "
    "multiple people, duplicate, clone, watermark, signature, text, username, writing, letters, words, error, "
    "(discolored nails:1.4), (bad nails:1.3), (deformed nails:1.3)"
)

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


# ------------------------------------------------------------------ #
#  Prompt building helpers (shared with hq_gen_worker.py)             #
# ------------------------------------------------------------------ #

_HAND_TRIGGERS = {"hand", "finger", "wave", "hold", "keyboard", "typing", "fanning", "umbrella", "mug", "drink"}


def build_full_prompt(scene_prompt: str, state: str) -> str:
    """Build the full SD prompt from a scene description and state key.

    Applies CHARACTER_PREFIX, period suffix, and conditional hand/ring anchor.
    """
    is_dark = state.endswith("_dark")
    suffix = PROMPT_SUFFIX_DARK if is_dark else PROMPT_SUFFIX
    full_prompt = f"{CHARACTER_PREFIX}, {scene_prompt}{suffix}"
    if any(t in full_prompt.lower() for t in _HAND_TRIGGERS):
        full_prompt += ", delicate silver ring on her finger, (five fingers:1.5)"
    return full_prompt


def build_negative_prompt(state: str, ti_prefix: str) -> str:
    """Build the negative prompt, adding dark-mode negatives when needed."""
    is_dark = state.endswith("_dark")
    if is_dark:
        return f"{ti_prefix}{NEGATIVE_PROMPT}, {DARK_NEGATIVE}"
    return f"{ti_prefix}{NEGATIVE_PROMPT}"


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
    _upgrade_queue: deque = deque()
    _upgrade_in_progress: set[str] = set()
    _upgrade_scheduler_started: bool = False
    _atexit_registered: bool = False
    _start_worker_lock: threading.Lock = threading.Lock()  # prevents concurrent spawns

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

        # Yield GPU from the worker so the fast image can generate immediately.
        # Priority marker covers the TOCTOU window (worker defers before locking).
        # Only SIGTERM the worker when it is actively holding the lock — killing it
        # during startup (before the custom handler is installed) causes silent death
        # and leaves a zombie that blocks re-spawning.
        cls._write_priority(state)
        if GPU_LOCK_PATH.exists():
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
                    full_prompt = build_full_prompt(scene_prompt, state)
                    negative = build_negative_prompt(state, cls._ti_negative_prefix)
                    print(f"[ImageGen] Generating '{state}'" + (" (dark)" if state.endswith("_dark") else ""))
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
    def claim_gpu(cls):
        """Context manager: acquire the GPU for a high-priority workload.

        Two-stage preemption:

        1. Write a priority marker (env/priority_queue/_claim.json) so the worker
           yields voluntarily right before calling gpu_lock().  This covers the
           TOCTOU window where the worker is computing embeddings (CPU-only, no lock
           held yet) and would otherwise race us to the lock — without killing it.

        2. If the worker is currently holding the lock (actively doing GPU inference)
           we send SIGTERM and restart it afterward.  Waiting for a 30-step diffusion
           run to finish would stall the caller for minutes; killing it is justified.

        Usage::
            with ImageGenService.claim_gpu():
                # high-priority GPU work here
        """
        _CLAIM_KEY = "_claim"

        @contextmanager
        def _ctx():
            worker_killed = False
            # Stage 1 — write priority marker before touching the lock.
            # The worker checks for priority requests right before gpu_lock(),
            # so this closes the TOCTOU window with zero wasted GPU work.
            cls._write_priority(_CLAIM_KEY)
            try:
                # Stage 2 — if the lock is already held by another process, it must
                # be the HQ worker (possibly with a stale PID file — don't rely on
                # the PID file matching the lock holder).  Kill it directly.
                # _claim_gpu() only calls us when _in_progress is empty, so Flask's
                # own SD pipeline is never the lock holder here.
                try:
                    lock_pid_int = int(GPU_LOCK_PATH.read_text().strip())
                    if lock_pid_int != os.getpid():
                        try:
                            os.kill(lock_pid_int, signal.SIGTERM)
                            worker_killed = True
                        except (ProcessLookupError, OSError):
                            pass
                        # Retire the PID file — the process is gone or going.
                        WORKER_PID_PATH.unlink(missing_ok=True)
                except (OSError, ValueError):
                    pass

                with gpu_lock():
                    yield
            finally:
                cls._clear_priority(_CLAIM_KEY)
                if worker_killed:
                    cls._start_hq_worker()

        return _ctx()

    @classmethod
    def _signal_worker(cls):
        """Send SIGTERM to the HQ worker — GPU kernel dies, flock released immediately.

        The PID file is deleted here so _start_hq_worker() knows to spawn a fresh
        process after generation — the old PID stays openable (zombie/handle) on both
        Linux and Windows, which would fool the alive check otherwise.
        """
        pid_file = WORKER_PID_PATH
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
        if GPU_LOCK_PATH.exists():
            cls._signal_worker()

        try:
            with cls._lock, gpu_lock():
                pipe = cls._get_pipeline()
                pipe.to(DEVICE)
                torch.cuda.empty_cache()
                try:
                    generator = torch.Generator(DEVICE).manual_seed(effective_seed)
                    full_prompt = build_full_prompt(scene_prompt, state)
                    negative = build_negative_prompt(state, cls._ti_negative_prefix)
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
    def _is_worker_healthy(cls) -> bool:
        """Return True if the HQ worker has written a fresh heartbeat recently.

        The worker writes env/hq_worker.heartbeat (epoch timestamp) at startup
        and on each main loop iteration.  A stale or absent heartbeat means the
        worker has died or not yet started.
        """
        if not WORKER_HEARTBEAT_PATH.exists():
            return False
        try:
            age = time.time() - float(WORKER_HEARTBEAT_PATH.read_text().strip())
            return age < _WORKER_HEARTBEAT_TTL
        except (ValueError, OSError):
            return False

    @classmethod
    def _cleanup_stale_lock_at_startup(cls):
        """Remove stale coordination files left by a previous server run."""
        if GPU_LOCK_PATH.exists():
            print("[ImageGen][WARN] Stale gpu.lock found at startup — removing.")
            GPU_LOCK_PATH.unlink(missing_ok=True)
        # Heartbeat from a previous run is not meaningful for the new process
        WORKER_HEARTBEAT_PATH.unlink(missing_ok=True)

    @classmethod
    def _cleanup_stale_priority_at_startup(cls):
        """Called once at startup: remove all priority marker files left by a previous crash.

        Priority files (env/priority_queue/*.json) are written by generate() and
        claim_gpu() as an in-flight signal to the HQ worker.  If the Flask process
        crashes or is killed while one is active, the file persists and the worker
        loops forever in the priority guard rather than processing jobs.

        At startup there are no active fast-gen requests, so every file here is stale.
        """
        if not PRIORITY_QUEUE_DIR.exists():
            return
        stale = list(PRIORITY_QUEUE_DIR.glob("*.json"))
        if stale:
            for f in stale:
                f.unlink(missing_ok=True)
            print(f"[ImageGen] Cleared {len(stale)} stale priority file(s) at startup.")

    @classmethod
    def start_upgrade_scheduler(cls):
        if cls._upgrade_scheduler_started:
            return
        cls._upgrade_scheduler_started = True
        cls._cleanup_stale_lock_at_startup()
        cls._cleanup_stale_priority_at_startup()
        WORKER_BOOT_PATH.unlink(missing_ok=True)

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
        """Start hq_gen_worker.py as a detached subprocess. Guarded by a heartbeat file.

        The threading lock prevents two rapid callers from both concluding the
        worker is dead and each spawning a new one.
        """
        import subprocess

        with cls._start_worker_lock:
            pid_file = WORKER_PID_PATH
            pid_file.parent.mkdir(parents=True, exist_ok=True)

            # Healthy heartbeat → worker is alive, nothing to do
            if cls._is_worker_healthy():
                return

            # No heartbeat yet — check whether we're still in the boot window
            if WORKER_BOOT_PATH.exists():
                try:
                    boot_age = time.time() - WORKER_BOOT_PATH.stat().st_mtime
                except OSError:
                    boot_age = 0
                if boot_age < 120:
                    return  # still booting, give it time
                print(f"[ImageGen] Worker stuck in boot ({boot_age:.0f}s) — respawning.")
                if pid_file.exists():
                    try:
                        pid = int(pid_file.read_text().strip())
                        os.kill(pid, signal.SIGTERM)
                    except (ProcessLookupError, ValueError, OSError):
                        pass
                pid_file.unlink(missing_ok=True)
                WORKER_BOOT_PATH.unlink(missing_ok=True)

            WORKER_BOOT_PATH.write_text(str(time.time()))
            worker = Path(__file__).parent / "hq_gen_worker.py"
            proc = subprocess.Popen(
                [sys.executable, str(worker)],
            )
            pid_file.write_text(str(proc.pid))
            print(f"[ImageGen] HQ worker spawned (proc.pid={proc.pid}), waiting for boot confirmation.")

            # Register the atexit handler only ONCE for the lifetime of this Flask process.
            # We must NOT capture `proc` in the closure — doing so creates a new closure per
            # worker restart, accumulating stale atexit handlers that send SIGTERM to recycled
            # PIDs and delete gpu.lock while the *current* worker still holds it.
            if not cls._atexit_registered:
                import atexit
                import signal as _signal

                _pid_file_path = pid_file  # stable Path reference; content changes with restarts

                def _shutdown_worker():
                    # Read the *current* PID from the file — not a stale closure value.
                    try:
                        current_pid = int(_pid_file_path.read_text().strip())
                        os.kill(current_pid, _signal.SIGTERM)
                        print(f"[ImageGen] HQ worker (PID {current_pid}) terminated.")
                    except (ProcessLookupError, OSError):
                        pass
                    except ValueError:
                        pass
                    _pid_file_path.unlink(missing_ok=True)
                    WORKER_BOOT_PATH.unlink(missing_ok=True)
                    WORKER_HEARTBEAT_PATH.unlink(missing_ok=True)
                    GPU_LOCK_PATH.unlink(missing_ok=True)

                atexit.register(_shutdown_worker)
                cls._atexit_registered = True

    @classmethod
    def _upgrade_loop(cls):
        _last_worker_check = 0.0
        while True:
            # Periodic worker health check — respawn if dead
            now = time.time()
            if now - _last_worker_check >= 60:
                cls._start_hq_worker()
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
