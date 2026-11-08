import os
import wave

import numpy as np

MODELS = {"fast": "small", "accurate": "large-v3-turbo"}


def speech_language_codes():
    from faster_whisper.tokenizer import _LANGUAGE_CODES

    return sorted(_LANGUAGE_CODES)


def read_pcm_wav(path):
    with wave.open(str(path), "rb") as audio:
        frames = audio.readframes(audio.getnframes())
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768


class WhisperTranscriber:
    """Speech-to-text on the CPU. Only the most recently used model stays in memory."""

    def __init__(self):
        self._name = None
        self._model = None

    def _load(self, quality):
        name = MODELS[quality]
        if name != self._name:
            from faster_whisper import WhisperModel

            self._model = None
            self._model = WhisperModel(
                name,
                device="cpu",
                compute_type="int8",
                cpu_threads=max((os.cpu_count() or 2) - 2, 1),
            )
            self._name = name
        return self._model

    def __call__(self, audio_path, language, quality, on_progress):
        segments, info = self._load(quality).transcribe(
            read_pcm_wav(audio_path),
            language=None if language == "auto" else language,
            word_timestamps=True,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        words = []
        for segment in segments:
            words.extend(
                {"start": w.start, "end": w.end, "text": w.word} for w in segment.words or []
            )
            if info.duration:
                on_progress(min(segment.end / info.duration, 1))
        return words, info.language
