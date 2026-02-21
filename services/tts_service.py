from TTS.api import TTS
from io import BytesIO
import soundfile as sf

# Initialize TTS model once
tts = TTS(model_name="tts_models/en/vctk/vits", progress_bar=False, gpu=False)

def generate_speech_audio(text: str) -> BytesIO:
    """
    Generate speech audio as a BytesIO WAV buffer from input text.
    """
    # Good ones: 1, 17, 22, 44, 93
    wav = tts.tts(text, speed=1.0, speaker=tts.speakers[22])
    audio_buffer = BytesIO()
    sf.write(audio_buffer, wav, 22050, format="WAV")
    audio_buffer.seek(0)
    return audio_buffer
