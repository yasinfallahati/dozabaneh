import wave

import numpy as np

from app.transcribe import read_pcm_wav


def test_wav_is_read_as_normalised_floats(tmp_path):
    path = tmp_path / "audio.wav"
    samples = np.array([0, 16384, -32768], dtype=np.int16)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(samples.tobytes())

    audio = read_pcm_wav(path)

    assert audio.dtype == np.float32
    assert audio.tolist() == [0.0, 0.5, -1.0]
