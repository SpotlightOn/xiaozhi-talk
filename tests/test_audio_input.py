"""Tests for the microphone framing and format conversion."""

from __future__ import annotations

import numpy as np
from PySide6.QtMultimedia import QAudioFormat

from xiaozhi_talk.audio_input import FrameBuffer, convert_to_frames
from xiaozhi_talk.voice import (
    FRAME_BYTES,
    FRAME_MS,
    FRAME_SAMPLES,
    SILENCE_FRAME,
    TAIL_FRAMES,
    silence,
)


def _format(rate: int, channels: int, sample_format: QAudioFormat.SampleFormat) -> QAudioFormat:
    audio_format = QAudioFormat()
    audio_format.setSampleRate(rate)
    audio_format.setChannelCount(channels)
    audio_format.setSampleFormat(sample_format)
    return audio_format


# ---------------------------------------------------------------------------
# frame geometry
# ---------------------------------------------------------------------------


def test_one_frame_is_sixty_milliseconds_of_24_khz_mono() -> None:
    assert FRAME_SAMPLES == 1440
    assert FRAME_BYTES == 2880
    assert len(SILENCE_FRAME) == FRAME_BYTES
    assert silence(3) == b"\x00" * (FRAME_BYTES * 3)


def test_the_tail_is_long_enough_to_end_the_turn() -> None:
    # Measured against the server: 1.2 s of silence was not enough for it to
    # decide that the user stopped talking, 2 s is.
    assert TAIL_FRAMES * FRAME_MS >= 2000


def test_nothing_is_emitted_before_a_frame_is_complete() -> None:
    buffer = FrameBuffer()

    assert buffer.feed(b"\x01" * (FRAME_BYTES - 1)) == []
    frames = buffer.feed(b"\x01")

    assert len(frames) == 1
    assert len(frames[0]) == FRAME_BYTES


def test_a_burst_is_cut_into_several_frames() -> None:
    buffer = FrameBuffer()

    frames = buffer.feed(b"\x01" * (FRAME_BYTES * 3 + 100))

    assert len(frames) == 3
    assert all(len(frame) == FRAME_BYTES for frame in frames)


def test_the_rest_of_a_recording_is_padded_with_silence() -> None:
    buffer = FrameBuffer()
    buffer.feed(b"\x01" * (FRAME_BYTES + 100))

    tail = buffer.flush()

    assert len(tail) == FRAME_BYTES
    assert tail[:100] == b"\x01" * 100
    assert tail[100:] == b"\x00" * (FRAME_BYTES - 100)


def test_flushing_without_a_remainder_returns_nothing() -> None:
    buffer = FrameBuffer()
    buffer.feed(b"\x01" * FRAME_BYTES)

    assert buffer.flush() == b""


# ---------------------------------------------------------------------------
# format conversion
# ---------------------------------------------------------------------------


def test_24_khz_mono_passes_through_untouched() -> None:
    data = b"\x01\x00" * 100

    assert convert_to_frames(data, _format(24000, 1, QAudioFormat.Int16)) == data


def test_a_device_rate_is_resampled_to_24_khz() -> None:
    data = b"\x01\x00" * 4800  # 100 ms at 48 kHz

    converted = convert_to_frames(data, _format(48000, 1, QAudioFormat.Int16))

    assert len(converted) == 2400 * 2  # 100 ms at 24 kHz


def test_stereo_is_mixed_down_to_one_channel() -> None:
    data = b"\x01\x00\x03\x00" * 100  # 100 stereo frames

    converted = convert_to_frames(data, _format(24000, 2, QAudioFormat.Int16))

    samples = np.frombuffer(converted, dtype=np.int16)
    assert samples.size == 100
    assert set(samples.tolist()) == {2}  # the mean of 1 and 3


def test_float_samples_become_sixteen_bit() -> None:
    data = np.full(10, 0.5, dtype=np.float32).tobytes()

    converted = convert_to_frames(data, _format(24000, 1, QAudioFormat.Float))

    samples = np.frombuffer(converted, dtype=np.int16)
    assert samples.size == 10
    assert samples[0] == 16383


def test_thirty_two_bit_samples_become_sixteen_bit() -> None:
    data = np.array([0x12340000, -0x12340000], dtype=np.int32).tobytes()

    converted = convert_to_frames(data, _format(24000, 1, QAudioFormat.Int32))

    samples = np.frombuffer(converted, dtype=np.int16)
    assert samples.tolist() == [0x1234, -0x1234]
