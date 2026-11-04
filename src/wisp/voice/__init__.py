"""Voice: local speech-to-text (faster-whisper) and pluggable text-to-speech."""

from wisp.voice.stt import Transcriber
from wisp.voice.tts import Speech, make_tts, split_sentences, strip_tags

__all__ = ["Speech", "Transcriber", "make_tts", "split_sentences", "strip_tags"]
