#!/usr/bin/env python3
"""
Standalone HQ image generation worker (SD 1.5, 768×768, 30 steps).

Auto-started by ImageGenService.start_upgrade_scheduler() as a subprocess.
Polls env/hq_queue/ for job files written by ImageGenService.generate().
Uses env/gpu.lock to coordinate GPU access with the Flask process.
Writes results to tmp/persona/{state}_hq.png.
"""
import json
import os
import signal
import sys
import time
import traceback
import warnings
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import services.log_config as log_config
log_config.configure()

warnings.filterwarnings("ignore", message="Token indices sequence length is longer than")

import torch
from compel import Compel, DiffusersTextualInversionManager
from diffusers import StableDiffusionPipeline, DPMSolverMultistepScheduler, AutoencoderKL

from agents.gpu_lock import WORKER_PID_PATH, WORKER_HEARTBEAT_PATH, gpu_lock, PRIORITY_QUEUE_DIR, cleanup_stale_priority
from agents.image_gen_service import (
    MODEL_ID, VAE_ID, FIXED_SEED, OUTPUT_DIR, HQ_QUEUE_DIR, UHQ_QUEUE_DIR,
    DEVICE, TORCH_DTYPE,
    ImageGenService,
    _load_textual_inversions,
    build_full_prompt, build_negative_prompt,
    WORKER_BOOT_PATH,
)

# ---------------------------------------------------------------------------
# Model config — edit here when switching models
# ---------------------------------------------------------------------------
HQ_MODEL_ID     = MODEL_ID          # inherit fast-path model; override to swap
HQ_VAE_ID       = VAE_ID            # set to None to use the model's built-in VAE
HQ_STEPS        = 30                # inference steps (SD 1.5 sweet spot: 20–40)
HQ_SIZE         = 768               # output resolution (square)
HQ_GUIDANCE     = 6.5               # CFG scale
HQ_CLIP_SKIP    = 2                 # CLIP skip layers (2 = standard for anime models)
# Scheduler kwargs forwarded to DPMSolverMultistepScheduler.from_config()
HQ_SCHEDULER_KW = dict(use_karras_sigmas=True, algorithm_type="dpmsolver++")
_MQ_CFG  = dict(tier="MQ",  steps=HQ_STEPS,  size=HQ_SIZE,  guidance=HQ_GUIDANCE,  clip_skip=HQ_CLIP_SKIP,  path_fn=ImageGenService._hq_path)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# UHQ config — set UHQ_MODEL_ID=None to reuse the MQ pipeline instance
# ---------------------------------------------------------------------------
UHQ_MODEL_ID     = None   # None → same model, no second load
UHQ_VAE_ID       = VAE_ID
UHQ_STEPS        = 40
UHQ_SIZE         = 1024
UHQ_GUIDANCE     = 6.5
UHQ_CLIP_SKIP    = 2
UHQ_SCHEDULER_KW = dict(use_karras_sigmas=True, algorithm_type="dpmsolver++")
_UHQ_CFG = dict(tier="UHQ", steps=UHQ_STEPS, size=UHQ_SIZE, guidance=UHQ_GUIDANCE, clip_skip=UHQ_CLIP_SKIP, path_fn=ImageGenService._uhq_path)
# ---------------------------------------------------------------------------

_pipeline: StableDiffusionPipeline | None = None
_compel: Compel | None = None
_uhq_pipeline: StableDiffusionPipeline | None = None
_uhq_compel: Compel | None = None
_ti_negative_prefix: str = ""


def _wlog(msg: str) -> None:
    """Timestamped stderr log, flushed immediately."""
    try:
        ts = datetime.now().strftime('%H:%M:%S')
        sys.stderr.write(f'[{ts}] {msg}\n')
        sys.stderr.flush()
    except OSError:
        pass


def _get_pipeline() -> tuple[StableDiffusionPipeline, Compel]:
    global _pipeline, _compel, _ti_negative_prefix
    if _pipeline is None:
        print(f"[HQWorker] Loading {HQ_MODEL_ID} at {HQ_SIZE}×{HQ_SIZE} (stays on CPU between jobs)...")
        vae = AutoencoderKL.from_pretrained(HQ_VAE_ID, torch_dtype=TORCH_DTYPE) if HQ_VAE_ID else None
        _pipeline = StableDiffusionPipeline.from_pretrained(
            HQ_MODEL_ID,
            vae=vae,
            torch_dtype=TORCH_DTYPE,
            safety_checker=None,
        )
        _pipeline.scheduler = DPMSolverMultistepScheduler.from_config(
            _pipeline.scheduler.config,
            **HQ_SCHEDULER_KW,
        )
        _pipeline.enable_attention_slicing()
        _ti_negative_prefix = _load_textual_inversions(_pipeline)
        _compel = Compel(
            tokenizer=_pipeline.tokenizer,
            text_encoder=_pipeline.text_encoder,
            textual_inversion_manager=DiffusersTextualInversionManager(_pipeline),
            truncate_long_prompts=False,
        )
        print("[HQWorker] Pipeline loaded.")
    return _pipeline, _compel


def _get_uhq_pipeline() -> tuple[StableDiffusionPipeline, Compel]:
    global _uhq_pipeline, _uhq_compel, _ti_negative_prefix
    if UHQ_MODEL_ID is None:
        # Reuse MQ pipeline — TI embeddings already loaded, prefix already set
        return _get_pipeline()
    if _uhq_pipeline is None:
        print(f"[HQWorker] Loading UHQ model {UHQ_MODEL_ID}...")
        vae = AutoencoderKL.from_pretrained(UHQ_VAE_ID, torch_dtype=TORCH_DTYPE) if UHQ_VAE_ID else None
        _uhq_pipeline = StableDiffusionPipeline.from_pretrained(
            UHQ_MODEL_ID,
            vae=vae,
            torch_dtype=TORCH_DTYPE,
            safety_checker=None,
        )
        _uhq_pipeline.scheduler = DPMSolverMultistepScheduler.from_config(
            _uhq_pipeline.scheduler.config,
            **UHQ_SCHEDULER_KW,
        )
        _uhq_pipeline.enable_attention_slicing()
        _ti_negative_prefix = _load_textual_inversions(_uhq_pipeline)
        _uhq_compel = Compel(
            tokenizer=_uhq_pipeline.tokenizer,
            text_encoder=_uhq_pipeline.text_encoder,
            textual_inversion_manager=DiffusersTextualInversionManager(_uhq_pipeline),
            truncate_long_prompts=False,
        )
        print("[HQWorker] UHQ pipeline loaded.")
    return _uhq_pipeline, _uhq_compel


def _has_priority_requests() -> bool:
    return PRIORITY_QUEUE_DIR.exists() and bool(list(PRIORITY_QUEUE_DIR.glob("*.json")))


def _process_job(cfg, get_pipe_fn, state, scene_prompt, seed, output_stem, logical_state, save_fn):
    """Shared generation body. cfg is _MQ_CFG or _UHQ_CFG; save_fn handles the result."""
    effective_state = logical_state or state
    tier = cfg["tier"]
    if output_stem:
        save_path = OUTPUT_DIR / f"{output_stem}.png"
        if save_path.exists():
            print(f"[HQWorker] Skipping '{output_stem}' — experiment file already exists.")
            return
    else:
        if cfg["path_fn"](state).exists():
            print(f"[HQWorker] Skipping '{state}' — {tier} already exists.")
            return

    pipe, compel = get_pipe_fn()

    full_prompt = build_full_prompt(scene_prompt, effective_state)
    negative    = build_negative_prompt(effective_state, _ti_negative_prefix)

    label = output_stem or f"'{state}'"
    print(f"[HQWorker] Generating {label} ({tier}) at {cfg['size']}×{cfg['size']}, {cfg['steps']} steps...")
    print(f"[HQWorker] Prompt: {full_prompt}")

    # Compute embeddings on CPU before acquiring the GPU lock.
    prompt_embeds = compel(full_prompt)
    negative_embeds = compel(negative)
    [prompt_embeds, negative_embeds] = compel.pad_conditioning_tensors_to_same_length(
        [prompt_embeds, negative_embeds]
    )

    # Final priority check — yield rather than race if claim_gpu() arrived during embedding.
    if _has_priority_requests():
        _wlog(f"[HQWorker] Priority request — deferring {tier} {label}.")
        return

    with gpu_lock():
        pipe.to(DEVICE)
        torch.cuda.empty_cache()
        try:
            generator = torch.Generator(DEVICE).manual_seed(seed)
            image = pipe(
                prompt_embeds=prompt_embeds.to(DEVICE),
                negative_prompt_embeds=negative_embeds.to(DEVICE),
                num_inference_steps=cfg["steps"],
                guidance_scale=cfg["guidance"],
                clip_skip=cfg["clip_skip"],
                generator=generator,
                width=cfg["size"],
                height=cfg["size"],
            ).images[0]
        finally:
            pipe.to('cpu')
            torch.cuda.empty_cache()

    if output_stem:
        from PIL.PngImagePlugin import PngInfo
        info = PngInfo()
        info.add_text("scene_prompt", scene_prompt)
        info.add_text("tier", f"experiment_{tier.lower()}")
        info.add_text("seed", str(seed))
        tmp = save_path.with_suffix('.tmp.png')
        image.save(tmp, pnginfo=info)
        tmp.rename(save_path)
        print(f"[HQWorker] Experiment {tier} saved: {save_path.name} ({save_path.stat().st_size // 1024}KB)")
    else:
        save_fn(state, image, scene_prompt)


def _process_mq(state: str, scene_prompt: str, seed: int = FIXED_SEED,
                output_stem: str | None = None, logical_state: str | None = None):
    def _save(s, img, prompt):
        ImageGenService._save_hq(s, img, prompt)
        if not ImageGenService._uhq_path(s).exists():
            ImageGenService._queue_uhq(s, prompt, seed=seed)
    _process_job(_MQ_CFG, _get_pipeline, state, scene_prompt, seed, output_stem, logical_state, _save)


def _process_uhq(state: str, scene_prompt: str, seed: int = FIXED_SEED,
                 output_stem: str | None = None, logical_state: str | None = None):
    _process_job(_UHQ_CFG, _get_uhq_pipeline, state, scene_prompt, seed, output_stem, logical_state, ImageGenService._save_uhq)


def _is_canonical_worker() -> bool:
    """Claim the PID file with our real PID, then verify we still own it.

    Flask's proc.pid may differ from our actual PID (e.g. wrapper scripts on
    WSL).  We overwrite the PID file with os.getpid(), wait briefly to let any
    concurrent worker do the same, then re-read.  Last writer wins.
    """
    my_pid = os.getpid()
    try:
        WORKER_PID_PATH.write_text(str(my_pid))
    except OSError:
        pass

    time.sleep(0.5)   # let any concurrent duplicate also write its PID

    try:
        file_pid = int(WORKER_PID_PATH.read_text().strip())
    except (ValueError, OSError):
        return True   # file vanished — assume we're the only one

    if file_pid == my_pid:
        return True

    _wlog(f"[HQWorker] Another worker (PID {file_pid}) claimed the PID file — exiting (my PID {my_pid}).")
    return False


def _install_sigterm_handler():
    """Install SIGTERM handler. Uses os._exit() to skip Python teardown and
    avoid blocking on torch.cuda.synchronize() inside an active inference call.
    """
    def _handle(signum, frame):
        _wlog(f"\n[HQWorker] SIGTERM received (PID {os.getpid()}) — cleaning up.")
        WORKER_BOOT_PATH.unlink(missing_ok=True)
        WORKER_HEARTBEAT_PATH.unlink(missing_ok=True)
        WORKER_PID_PATH.unlink(missing_ok=True)
        os._exit(0)

    signal.signal(signal.SIGTERM, _handle)


def _heartbeat_loop():
    while True:
        time.sleep(30)
        try:
            WORKER_HEARTBEAT_PATH.write_text(str(time.time()))
        except OSError:
            pass


def _dispatch_queue(queue_dir: Path, process_fn, tier_label: str) -> bool:
    """Process the first pending job in queue_dir. Returns True if a job was dispatched."""
    jobs = sorted(queue_dir.glob("*.json"))
    if not jobs:
        return False
    job_file = jobs[0]
    state = job_file.stem
    try:
        data = json.loads(job_file.read_text())
        process_fn(state, data["scene_prompt"], seed=data.get("seed", FIXED_SEED),
                   output_stem=data.get("output_stem"), logical_state=data.get("state"))
        job_file.unlink(missing_ok=True)  # unlink AFTER success — survives SIGTERM
    except Exception as e:
        _wlog(f"[HQWorker][ERROR] {tier_label} job '{state}' failed ({type(e).__name__}): {e}")
        try:
            traceback.print_exc(file=sys.stderr)
        except OSError:
            pass
        job_file.unlink(missing_ok=True)
    return True


def main():
    if not _is_canonical_worker():
        WORKER_BOOT_PATH.unlink(missing_ok=True)
        sys.exit(0)
    _install_sigterm_handler()
    WORKER_BOOT_PATH.unlink(missing_ok=True)
    # Write initial heartbeat so Flask sees us as healthy immediately after boot
    WORKER_HEARTBEAT_PATH.write_text(str(time.time()))
    _wlog(f"[HQWorker] Boot complete (PID {os.getpid()}).")

    # Heartbeat thread: main loop blocks for the full SD run, so update from a thread.
    import threading as _threading
    _threading.Thread(target=_heartbeat_loop, daemon=True).start()

    HQ_QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    UHQ_QUEUE_DIR.mkdir(parents=True, exist_ok=True)

    # Clear stale priority files from a previous Flask crash (Flask does this too, belt-and-suspenders).
    cleanup_stale_priority()

    _wlog(f"[HQWorker] Started (PID {os.getpid()}). Watching MQ and UHQ queues...")

    try:
        while True:
            # Priority guard: Flask is generating — do not touch the GPU
            if _has_priority_requests():
                time.sleep(2)
                continue
            if _dispatch_queue(HQ_QUEUE_DIR, _process_mq, "MQ"):
                continue
            if _dispatch_queue(UHQ_QUEUE_DIR, _process_uhq, "UHQ"):
                continue
            time.sleep(30)

    except (KeyboardInterrupt, SystemExit):
        pass
    except BaseException as e:
        _wlog(f"[HQWorker][FATAL] Unhandled {type(e).__name__}: {e}")
        try:
            traceback.print_exc(file=sys.stderr)
        except OSError:
            pass


if __name__ == "__main__":
    main()
