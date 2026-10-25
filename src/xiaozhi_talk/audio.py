"""Playback of the decoded voice replies.

The SDK decodes incoming Opus frames to PCM and appends them to a deque.  The
GUI drains that deque on a timer and pushes the samples into a ``QAudioSink``.
"""

from __future__ import annotations

import contextlib
import logging
from collections import deque
from typing import Any

from PySide6.QtCore import QObject

logger = logging.getLogger(__name__)

try:  # Qt Multimedia is missing on some minimal installations
    from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices

    MULTIMEDIA_AVAILABLE = True
except Exception as exc:  # pragma: no cover - depends on the installation
    logger.info("Qt Multimedia is not available, voice playback is disabled: %s", exc)
    MULTIMEDIA_AVAILABLE = False


def _frame_bytes(item: Any) -> bytes:
    """Convert one queued audio item to raw little endian int16 bytes."""
    if isinstance(item, (bytes, bytearray, memoryview)):
        return bytes(item)
    try:
        import numpy as np
    except ImportError:  # pragma: no cover - numpy ships with the SDK
        return b""
    array = np.asarray(item)
    if array.dtype != np.int16:
        array = array.astype(np.int16)
    return array.tobytes()


class VoicePlayer(QObject):
    """Small push-mode audio player for the server's TTS stream."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._sink: QAudioSink | None = None
        self._device: Any | None = None
        self._sample_rate = 0
        self._pending = b""
        self._warned = False
        self._enabled = True
        self._buffer_bytes = 0

    # -- public API ---------------------------------------------------------

    @property
    def busy(self) -> bool:
        """True while samples are still queued or the device is behind."""
        if self._pending:
            return True
        if self._sink is None or self._device is None:
            return False
        try:
            return self._sink.bytesFree() < self._buffer_bytes
        except Exception:  # pragma: no cover - device disappeared
            return False

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if not enabled:
            self.stop()

    def pump(self, queue: deque[Any] | None, sample_rate: int) -> None:
        """Move everything currently queued into the audio device."""
        if not self._enabled or queue is None:
            return
        chunks = []
        while True:
            try:
                chunks.append(_frame_bytes(queue.popleft()))
            except IndexError:
                break
        if chunks:
            self._pending += b"".join(chunks)

        if not self._pending:
            return

        device = self._ensure_device(sample_rate)
        if device is None:
            self._pending = b""
            return
        data = self._to_device_channels(self._pending)
        if data is None:
            self._pending = b""
            return
        try:
            written = device.write(data)
        except Exception as exc:  # pragma: no cover - device disappeared
            logger.debug("Audio write failed: %s", exc)
            self.stop()
            return
        if written and written > 0:
            # The device counts samples in its own channel layout.
            accepted = written // 2
            consumed = (accepted // self._channels) * 2
            self._pending = self._pending[consumed:]

    def stop(self) -> None:
        """Stop playback and drop queued samples."""
        self._pending = b""
        if self._sink is not None:
            with contextlib.suppress(Exception):  # pragma: no cover
                self._sink.stop()
            self._sink.deleteLater()
        self._sink = None
        self._device = None
        self._sample_rate = 0
        self._buffer_bytes = 0
        self._channels = 1

    # -- internals ----------------------------------------------------------

    def _ensure_device(self, sample_rate: int) -> Any | None:
        if self._sink is not None and self._sample_rate == sample_rate:
            return self._device
        self.stop()
        if not MULTIMEDIA_AVAILABLE:
            return None

        output = QMediaDevices.defaultAudioOutput()
        if output is None or output.isNull():
            if not self._warned:
                logger.info("No audio output device available, replies stay silent")
                self._warned = True
            return None

        audio_format = QAudioFormat()
        audio_format.setSampleRate(sample_rate)
        audio_format.setSampleFormat(QAudioFormat.Int16)
        # Mono is often played on the left channel only (PipeWire does not
        # remix it), so the reply goes out as stereo and is duplicated below.
        audio_format.setChannelCount(2)
        if not output.isFormatSupported(audio_format):
            audio_format.setChannelCount(1)
        channels = audio_format.channelCount()

        sink = QAudioSink(output, audio_format)
        # ~250 ms of buffer keeps the stream stable without much latency.
        self._buffer_bytes = int(sample_rate * 2 * 0.25)
        sink.setBufferSize(self._buffer_bytes)
        device = sink.start()
        if device is None:
            sink.deleteLater()
            if not self._warned:
                logger.info("The audio device could not be opened, replies stay silent")
                self._warned = True
            return None

        self._sink = sink
        self._device = device
        self._sample_rate = sample_rate
        self._channels = channels
        return device

    def _to_device_channels(self, data: bytes) -> bytes | None:
        """Copy mono samples into every channel the device expects."""
        if self._channels <= 1:
            return data
        try:
            import numpy as np
        except ImportError:  # pragma: no cover - numpy ships with the SDK
            return None
        samples = np.frombuffer(data, dtype=np.int16)
        return np.repeat(samples, self._channels).tobytes()
