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
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

warnings.filterwarnings("ignore", message="Token indices sequence length is longer than")

import torch
from compel import Compel, DiffusersTextualInversionManager
from diffusers import StableDiffusionPipeline, DPMSolverMultistepScheduler, AutoencoderKL

from agents.persona_states import CHARACTER_PREFIX
from agents.image_gen_service import (
    MODEL_ID, VAE_ID, FIXED_SEED, OUTPUT_DIR, HQ_QUEUE_DIR, UHQ_QUEUE_DIR, PRIORITY_QUEUE_DIR,
    PROMPT_SUFFIX, PROMPT_SUFFIX_DARK,
    NEGATIVE_PROMPT, DARK_NEGATIVE,
    DEVICE, TORCH_DTYPE,
    ImageGenService, gpu_lock,
    _load_textual_inversions,
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
# ---------------------------------------------------------------------------

_pipeline: StableDiffusionPipeline | None = None
_compel: Compel | None = None
_uhq_pipeline: StableDiffusionPipeline | None = None
_uhq_compel: Compel | None = None
_ti_negative_prefix: str = ""


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


def _process_mq(state: str, scene_prompt: str, seed: int = FIXED_SEED,
                output_stem: str | None = None, logical_state: str | None = None):
    # output_stem set → experiment job: save to custom path, no UHQ queue
    effective_state = logical_state or state
    if output_stem:
        save_path = OUTPUT_DIR / f"{output_stem}.png"
        if save_path.exists():
            print(f"[HQWorker] Skipping '{output_stem}' — experiment file already exists.")
            return
    else:
        hq_path = ImageGenService._hq_path(state)
        if hq_path.exists():
            print(f"[HQWorker] Skipping '{state}' — HQ already exists.")
            return

    pipe, compel = _get_pipeline()

    is_dark     = effective_state.endswith("_dark")
    suffix      = PROMPT_SUFFIX_DARK if is_dark else PROMPT_SUFFIX
    negative    = f"{_ti_negative_prefix}{NEGATIVE_PROMPT}, {DARK_NEGATIVE}" if is_dark else f"{_ti_negative_prefix}{NEGATIVE_PROMPT}"
    full_prompt = f"{CHARACTER_PREFIX}, {scene_prompt}{suffix}"
    _hand_triggers = {"hand", "finger", "wave", "hold", "keyboard", "typing", "fanning", "umbrella", "mug", "drink"}
    if any(t in full_prompt.lower() for t in _hand_triggers):
        full_prompt += ", delicate silver ring on her finger, (five fingers:1.5)"

    label = output_stem or f"'{state}'"
    print(f"[HQWorker] Generating {label} (MQ) at {HQ_SIZE}×{HQ_SIZE}, {HQ_STEPS} steps...")
    print(f"[HQWorker] Prompt: {full_prompt}")

    # Compute embeddings while text encoders are still on CPU —
    # tokenizer output is always CPU, so this avoids a device mismatch.
    prompt_embeds = compel(full_prompt)
    negative_embeds = compel(negative)
    [prompt_embeds, negative_embeds] = compel.pad_conditioning_tensors_to_same_length(
        [prompt_embeds, negative_embeds]
    )

    with gpu_lock():
        pipe.to(DEVICE)
        torch.cuda.empty_cache()
        try:
            generator = torch.Generator(DEVICE).manual_seed(seed)
            image = pipe(
                prompt_embeds=prompt_embeds.to(DEVICE),
                negative_prompt_embeds=negative_embeds.to(DEVICE),
                num_inference_steps=HQ_STEPS,
                guidance_scale=HQ_GUIDANCE,
                clip_skip=HQ_CLIP_SKIP,
                generator=generator,
                width=HQ_SIZE,
                height=HQ_SIZE,
            ).images[0]
        finally:
            pipe.to('cpu')
            torch.cuda.empty_cache()

    if output_stem:
        from PIL.PngImagePlugin import PngInfo
        info = PngInfo()
        info.add_text("scene_prompt", scene_prompt)
        info.add_text("tier", "experiment_mq")
        info.add_text("seed", str(seed))
        tmp = save_path.with_suffix('.tmp.png')
        image.save(tmp, pnginfo=info)
        tmp.rename(save_path)
        print(f"[HQWorker] Experiment MQ saved: {save_path.name} ({save_path.stat().st_size // 1024}KB)")
    else:
        ImageGenService._save_hq(state, image, scene_prompt)
        # Queue UHQ upgrade if not already present
        if not ImageGenService._uhq_path(state).exists():
            ImageGenService._queue_uhq(state, scene_prompt, seed=seed)


def _process_uhq(state: str, scene_prompt: str, seed: int = FIXED_SEED,
                 output_stem: str | None = None, logical_state: str | None = None):
    effective_state = logical_state or state
    if output_stem:
        save_path = OUTPUT_DIR / f"{output_stem}.png"
        if save_path.exists():
            print(f"[HQWorker] Skipping '{output_stem}' — experiment file already exists.")
            return
    else:
        uhq_path = ImageGenService._uhq_path(state)
        if uhq_path.exists():
            print(f"[HQWorker] Skipping '{state}' — UHQ already exists.")
            return

    pipe, compel = _get_uhq_pipeline()

    is_dark     = effective_state.endswith("_dark")
    suffix      = PROMPT_SUFFIX_DARK if is_dark else PROMPT_SUFFIX
    negative    = f"{_ti_negative_prefix}{NEGATIVE_PROMPT}, {DARK_NEGATIVE}" if is_dark else f"{_ti_negative_prefix}{NEGATIVE_PROMPT}"
    full_prompt = f"{CHARACTER_PREFIX}, {scene_prompt}{suffix}"
    _hand_triggers = {"hand", "finger", "wave", "hold", "keyboard", "typing", "fanning", "umbrella", "mug", "drink"}
    if any(t in full_prompt.lower() for t in _hand_triggers):
        full_prompt += ", delicate silver ring on her finger, (five fingers:1.5)"

    label = output_stem or f"'{state}'"
    print(f"[HQWorker] Generating {label} (UHQ) at {UHQ_SIZE}×{UHQ_SIZE}, {UHQ_STEPS} steps...")
    print(f"[HQWorker] Prompt: {full_prompt}")

    prompt_embeds = compel(full_prompt)
    negative_embeds = compel(negative)
    [prompt_embeds, negative_embeds] = compel.pad_conditioning_tensors_to_same_length(
        [prompt_embeds, negative_embeds]
    )

    with gpu_lock():
        pipe.to(DEVICE)
        torch.cuda.empty_cache()
        try:
            generator = torch.Generator(DEVICE).manual_seed(seed)
            image = pipe(
                prompt_embeds=prompt_embeds.to(DEVICE),
                negative_prompt_embeds=negative_embeds.to(DEVICE),
                num_inference_steps=UHQ_STEPS,
                guidance_scale=UHQ_GUIDANCE,
                clip_skip=UHQ_CLIP_SKIP,
                generator=generator,
                width=UHQ_SIZE,
                height=UHQ_SIZE,
            ).images[0]
        finally:
            pipe.to('cpu')
            torch.cuda.empty_cache()

    if output_stem:
        from PIL.PngImagePlugin import PngInfo
        info = PngInfo()
        info.add_text("scene_prompt", scene_prompt)
        info.add_text("tier", "experiment_uhq")
        info.add_text("seed", str(seed))
        tmp = save_path.with_suffix('.tmp.png')
        image.save(tmp, pnginfo=info)
        tmp.rename(save_path)
        print(f"[HQWorker] Experiment UHQ saved: {save_path.name} ({save_path.stat().st_size // 1024}KB)")
    else:
        ImageGenService._save_uhq(state, image, scene_prompt)


def _install_sigterm_handler():
    """Install a graceful SIGTERM handler.

    Uses os._exit() rather than sys.exit() to avoid blocking on Python cleanup
    (in particular, the 'finally: pipe.to(cpu)' inside an active inference call
    would call torch.cuda.synchronize() and stall for the full step duration).
    The OS releases the gpu.lock flock instantly when the process exits, so
    Flask's gpu_lock() unblocks in milliseconds regardless of what the GPU was doing.
    """
    def _handle(signum, frame):
        print(f"\n[HQWorker] SIGTERM received (PID {os.getpid()}) — cleaning up.")
        Path("env/hq_worker.pid").unlink(missing_ok=True)
        GPU_LOCK_PATH.unlink(missing_ok=True)
        os._exit(0)

    signal.signal(signal.SIGTERM, _handle)


def main():
    _install_sigterm_handler()
    HQ_QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    UHQ_QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[HQWorker] Started (PID {os.getpid()}). Watching MQ and UHQ queues...")

    while True:
        # Priority guard: Flask is generating — do not touch the GPU
        if _has_priority_requests():
            time.sleep(2)
            continue

        # MQ jobs take precedence over UHQ
        mq_jobs = sorted(HQ_QUEUE_DIR.glob("*.json"))
        if mq_jobs:
            job_file = mq_jobs[0]
            state = job_file.stem
            try:
                data = json.loads(job_file.read_text())
                _process_mq(state, data["scene_prompt"], seed=data.get("seed", FIXED_SEED),
                            output_stem=data.get("output_stem"),
                            logical_state=data.get("state"))
                job_file.unlink(missing_ok=True)  # unlink AFTER success — survives SIGTERM
            except Exception as e:
                print(f"[HQWorker] MQ job '{state}' failed: {e}")
                job_file.unlink(missing_ok=True)
            continue

        uhq_jobs = sorted(UHQ_QUEUE_DIR.glob("*.json"))
        if uhq_jobs:
            job_file = uhq_jobs[0]
            state = job_file.stem
            try:
                data = json.loads(job_file.read_text())
                _process_uhq(state, data["scene_prompt"], seed=data.get("seed", FIXED_SEED),
                             output_stem=data.get("output_stem"),
                             logical_state=data.get("state"))
                job_file.unlink(missing_ok=True)  # unlink AFTER success — survives SIGTERM
            except Exception as e:
                print(f"[HQWorker] UHQ job '{state}' failed: {e}")
                job_file.unlink(missing_ok=True)
            continue

        time.sleep(30)


if __name__ == "__main__":
    main()
