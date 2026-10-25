"""Frame geometry of the audio the client sends to a Xiaozhi server.

The SDK turns the PCM it receives into 16 kHz Opus and expects exactly one
frame of 60 ms per call, so the numbers live here: the capture module cuts the
microphone stream with them and the client appends the trailing silence that
ends a voice turn.  This module deliberately has no imports so that both the
Qt based capture and the Qt free client can use it.
"""

from __future__ import annotations

#: Sample rate the server expects from the device side.
SAMPLE_RATE = 24000

#: Duration of one frame the SDK accepts.
FRAME_MS = 60

#: Samples and bytes in one frame (mono, 16 bit).
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
FRAME_BYTES = FRAME_SAMPLES * 2

#: One frame of digital silence.
SILENCE_FRAME = bytes(FRAME_BYTES)

#: Frames of silence sent after a recording.  The server decides that the user
#: stopped talking when the microphone goes quiet, so a recording without this
#: tail is never transcribed.  Measured against the server: 1.2 s is too short
#: and the transcription never arrives, 2 s works reliably.
TAIL_FRAMES = 34


#: Hands-free: loud frames that start an utterance.
SPEECH_FRAMES = 3

#: Hands-free: quiet frames that end it again (2.04 s at 60 ms per frame).
PAUSE_FRAMES = 34

#: Hands-free: bottom of what counts as speech, as RMS of full scale.
SPEECH_LEVEL = 0.012

#: Hands-free: frames kept from before the first word, so nothing is lost.
PREBUFFER_FRAMES = 12


def silence(frames: int = TAIL_FRAMES) -> bytes:
    """Return ``frames`` frames of silence."""
    return SILENCE_FRAME * max(0, frames)


def is_speech(frame: bytes, level: float = SPEECH_LEVEL) -> bool:
    """True when ``frame`` is louder than room noise.

    Hands-free mode uses this to notice that somebody started talking and that
    they stopped again; the numbers are RMS of full scale.
    """
    try:
        import numpy as np
    except ImportError:  # pragma: no cover - numpy ships with the SDK
        return False
    samples = np.frombuffer(frame, dtype=np.int16)
    if samples.size == 0:
        return False
    rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2))) / 32768.0
    return rms >= level
