"""Prompt-building helpers shared by image_gen_service and hq_gen_worker."""
from pathlib import Path

from agents.persona.states import CHARACTER_PREFIX

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
