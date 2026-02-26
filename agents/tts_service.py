import re
from io import BytesIO
import soundfile as sf

# Switch TTS backend: 'kokoro' or 'coqui'
TTS_BACKEND = 'kokoro'

# Kokoro voice — swap this to audition options (restart required):
#   af_sky, af_nicole, af_bella, af_sarah, af_heart  (American female)
#   bf_emma, bf_isabella                              (British female)
KOKORO_VOICE = 'bf_isabella'

# Kokoro speed — 1.0 is neutral; 1.1–1.3 sounds more energetic
KOKORO_SPEED = 1.1

# Pitch shift in semitones applied after generation (0 = off, 2–5 = noticeably higher/cuter)
# Note: resampling also slightly increases tempo; lower KOKORO_SPEED a touch to compensate if needed
KOKORO_PITCH = 2

# Lazy instances — only the active backend is loaded
_kokoro_pipeline = None
_coqui_tts = None


def _get_kokoro():
    global _kokoro_pipeline
    if _kokoro_pipeline is None:
        from kokoro import KPipeline
        # lang_code is the first char of the voice name: 'a' = American, 'b' = British
        lang_code = KOKORO_VOICE[0]
        _kokoro_pipeline = KPipeline(lang_code=lang_code)
    return _kokoro_pipeline


def _get_coqui():
    global _coqui_tts
    if _coqui_tts is None:
        from TTS.api import TTS
        _coqui_tts = TTS(model_name="tts_models/en/vctk/vits", progress_bar=False, gpu=False)
    return _coqui_tts


def _clean_for_tts(text: str) -> str:
    """Normalise LLM/anime-style text quirks that confuse TTS models."""
    # Remove decorative tilde (Japanese-style trailing flourish)
    text = text.replace('~', '')
    # Remove stutter notation: "C-cold" → "cold"
    text = re.sub(r'\b[A-Za-z]-(?=[A-Za-z])', '', text)
    # Collapse 3+ repeated characters to 2: "Brrr" → "Br", "Hmmm" → "Hm"
    text = re.sub(r'(.)\1{2,}', r'\1\1', text)
    # Remove consonant-only words — unpronounceable clusters like "Br", "Hmph", "Pfft"
    # Excludes y/Y (acts as vowel in "why", "by", etc.)
    # Negative lookbehind on apostrophe prevents stripping trailing parts of contractions ("it's", "don't")
    text = re.sub(r"(?<!')\b[bcdfghjklmnpqrstvwxzBCDFGHJKLMNPQRSTVWXZ]+\b", '', text)
    # Strip leftover leading punctuation/whitespace after removals
    text = re.sub(r'^[\s,\.…\-!?]+', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def generate_speech_audio(text: str) -> BytesIO:
    text = _clean_for_tts(text)
    if TTS_BACKEND == 'kokoro':
        return _generate_kokoro(text)
    return _generate_coqui(text)


def _generate_kokoro(text: str) -> BytesIO:
    import numpy as np
    pipeline = _get_kokoro()
    chunks = [audio for _, _, audio in pipeline(text, voice=KOKORO_VOICE, speed=KOKORO_SPEED)]
    audio_data = np.concatenate(chunks)

    if KOKORO_PITCH:
        from pedalboard import Pedalboard, PitchShift
        board = Pedalboard([PitchShift(semitones=KOKORO_PITCH)])
        audio_data = board(audio_data, 24000)

    buf = BytesIO()
    sf.write(buf, audio_data, 24000, format='WAV')
    buf.seek(0)
    return buf


def _generate_coqui(text: str) -> BytesIO:
    tts = _get_coqui()
    # Good speakers: 1, 17, 22, 44, 93
    wav = tts.tts(text, speed=1.0, speaker=tts.speakers[22])
    buf = BytesIO()
    sf.write(buf, wav, 22050, format='WAV')
    buf.seek(0)
    return buf
