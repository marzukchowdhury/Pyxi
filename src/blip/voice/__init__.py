"""Voice: local speech-to-text (faster-whisper) and pluggable text-to-speech."""

from blip.voice.stt import Transcriber
from blip.voice.tts import Speech, make_tts, split_sentences, strip_tags

__all__ = ["Speech", "Transcriber", "make_tts", "split_sentences", "strip_tags"]
