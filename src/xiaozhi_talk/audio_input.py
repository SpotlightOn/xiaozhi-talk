"""Microphone capture for voice input.

Qt Multimedia hides the platform audio API behind one interface, so the same
code records through PipeWire, PulseAudio, ALSA, WASAPI or CoreAudio.  The
server wants 24 kHz mono 16 bit PCM in frames of exactly 60 ms, so the raw
stream is converted and cut into frames here before it goes to the client.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Signal

from .voice import FRAME_BYTES, SAMPLE_RATE

logger = logging.getLogger(__name__)

try:  # Qt Multimedia is missing on some minimal installations
    from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices

    MULTIMEDIA_AVAILABLE = True
except Exception as exc:  # pragma: no cover - depends on the installation
    logger.info("Qt Multimedia is not available, voice input is disabled: %s", exc)
    MULTIMEDIA_AVAILABLE = False

try:  # numpy arrives with xiaozhi-sdk; without it only 24 kHz mono works
    import numpy as np

    NUMPY_AVAILABLE = True
except Exception:  # pragma: no cover - depends on the installation
    np = None  # type: ignore[assignment]
    NUMPY_AVAILABLE = False


class FrameBuffer:
    """Cuts an arbitrary byte stream into frames of exactly ``frame_bytes``."""

    def __init__(self, frame_bytes: int = FRAME_BYTES) -> None:
        self.frame_bytes = frame_bytes
        self._pending = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        """Add ``data`` and return every frame that is complete now."""
        self._pending.extend(data)
        size = self.frame_bytes
        frames: list[bytes] = []
        while len(self._pending) >= size:
            frames.append(bytes(self._pending[:size]))
            del self._pending[:size]
        return frames

    def flush(self) -> bytes:
        """Return the last, incomplete frame padded with silence, if any."""
        if not self._pending:
            return b""
        data = bytes(self._pending)
        self._pending.clear()
        return data + bytes(self.frame_bytes - len(data))


def input_devices() -> list[tuple[str, str]]:
    """Return ``(identifier, description)`` for every recording device."""
    if not MULTIMEDIA_AVAILABLE:
        return []
    devices = []
    for device in QMediaDevices.audioInputs():
        if device.isNull():
            continue
        devices.append((_device_id(device), device.description()))
    return devices


def find_input_device(identifier: str = "") -> Any | None:
    """Return the device with ``identifier``, the default one, or ``None``."""
    if not MULTIMEDIA_AVAILABLE:
        return None
    devices = [device for device in QMediaDevices.audioInputs() if not device.isNull()]
    if identifier:
        for device in devices:
            if _device_id(device) == identifier:
                return device
    default = QMediaDevices.defaultAudioInput()
    if default is not None and not default.isNull():
        return default
    return devices[0] if devices else None


def _device_id(device: Any) -> str:
    """A stable, readable identifier for a device."""
    raw = device.id()
    with contextlib.suppress(Exception):
        return bytes(raw).decode("utf-8", "replace")
    return str(raw)  # pragma: no cover - only for exotic backends


def _preferred_format(device: Any) -> Any:
    """24 kHz mono 16 bit when the device can do it, otherwise its own format."""
    wanted = QAudioFormat()
    wanted.setSampleRate(SAMPLE_RATE)
    wanted.setChannelCount(1)
    wanted.setSampleFormat(QAudioFormat.Int16)
    if device.isFormatSupported(wanted):
        return wanted
    return device.preferredFormat()


def _to_mono_int16(data: bytes, audio_format: Any) -> Any | None:
    """Convert raw samples to a numpy array of mono 16 bit samples."""
    if not NUMPY_AVAILABLE:  # pragma: no cover - numpy ships with the SDK
        return None
    kind = audio_format.sampleFormat()
    if kind == QAudioFormat.Int16:
        samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
    elif kind == QAudioFormat.UInt8:
        samples = (np.frombuffer(data, dtype=np.uint8).astype(np.float32) - 128.0) * 256.0
    elif kind == QAudioFormat.Int32:
        samples = (np.frombuffer(data, dtype=np.int32) >> 16).astype(np.float32)
    elif kind == QAudioFormat.Float:
        samples = np.frombuffer(data, dtype=np.float32) * 32767.0
    else:  # pragma: no cover - no backend uses this for recording
        logger.debug("Unsupported sample format %s", kind)
        return None

    channels = max(1, audio_format.channelCount())
    if channels > 1:
        usable = samples.size - samples.size % channels
        samples = samples[:usable].reshape(-1, channels).mean(axis=1)
    return np.clip(samples, -32768.0, 32767.0)


def convert_to_frames(data: bytes, audio_format: Any) -> bytes:
    """Convert recorded bytes to 24 kHz mono 16 bit PCM."""
    if (
        audio_format.sampleFormat() == QAudioFormat.Int16
        and audio_format.channelCount() == 1
        and audio_format.sampleRate() == SAMPLE_RATE
    ):
        return data  # the common case: the device does the work for us

    samples = _to_mono_int16(data, audio_format)
    if samples is None:  # pragma: no cover - without numpy nothing is usable
        return b""
    rate = audio_format.sampleRate()
    if rate and rate != SAMPLE_RATE:
        count = int(round(samples.size * SAMPLE_RATE / rate))
        if count <= 0:
            return b""
        positions = np.linspace(0.0, samples.size - 1.0, count, dtype=np.float32)
        samples = np.interp(positions, np.arange(samples.size, dtype=np.float32), samples)
    return samples.astype(np.int16).tobytes()


def _level(data: bytes) -> float:
    """The loudness of ``data`` as a fraction of full scale (0.0 to 1.0)."""
    if not data or not NUMPY_AVAILABLE:  # pragma: no cover - numpy ships with the SDK
        return 0.0
    samples = np.frombuffer(data, dtype=np.int16)
    if samples.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
    return min(1.0, rms / 32768.0)


class MicrophoneCapture(QObject):
    """Records from one input device and emits ready made frames."""

    frameReady = Signal(bytes)
    #: Loudness of the latest slice, as a fraction of full scale.
    level = Signal(float)
    started = Signal()
    stopped = Signal()
    failed = Signal(str)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        source_factory: Callable[[Any, Any], Any] | None = None,
    ) -> None:
        super().__init__(parent)
        self._source: Any | None = None
        self._io: Any | None = None
        self._format: Any | None = None
        self._buffer = FrameBuffer()
        self._source_factory = source_factory
        self._peak = 0.0

    @property
    def recording(self) -> bool:
        return self._source is not None

    @property
    def peak(self) -> float:
        """The loudest level since the recording started (0.0 to 1.0)."""
        return self._peak

    def start(self, device_id: str = "") -> bool:
        """Begin recording; returns ``False`` and reports why it failed."""
        if self._source is not None:
            return True
        if not MULTIMEDIA_AVAILABLE:
            self.failed.emit(
                "Voice input is not available because Qt Multimedia is missing. "
                "Install it or use the text box instead."
            )
            return False

        device = find_input_device(device_id)
        if device is None:
            self.failed.emit("No microphone was found. Connect one and try again.")
            return False

        audio_format = _preferred_format(device)
        try:
            factory = self._source_factory or QAudioSource
            source = factory(device, audio_format)
        except Exception as exc:
            logger.exception("Opening the microphone failed")
            self.failed.emit(f'The microphone "{device.description()}" could not be opened: {exc}')
            return False

        # ~200 ms of buffer: enough to survive a scheduling hiccup, short
        # enough that the recording still feels live.
        source.setBufferSize(int(SAMPLE_RATE * 2 * 0.2))
        try:
            io = source.start()
        except Exception as exc:  # pragma: no cover - backend specific
            logger.exception("Starting the microphone failed")
            source.deleteLater()
            self.failed.emit(f'The microphone "{device.description()}" could not be started: {exc}')
            return False
        if io is None:
            source.deleteLater()
            self.failed.emit(
                f'The microphone "{device.description()}" could not be started. '
                "It may be in use by another program."
            )
            return False

        io.readyRead.connect(self._read)
        self._source = source
        self._io = io
        self._format = audio_format
        self._buffer = FrameBuffer()
        self._peak = 0.0
        logger.debug(
            "Recording from %s at %s Hz, %s channel(s), %s",
            device.description(),
            audio_format.sampleRate(),
            audio_format.channelCount(),
            audio_format.sampleFormat(),
        )
        self.started.emit()
        return True

    def stop(self) -> None:
        """Stop recording and emit the last, padded frame."""
        source, io = self._source, self._io
        if source is None:
            return
        self._source = None
        self._io = None
        if io is not None:
            with contextlib.suppress(Exception):
                io.readyRead.disconnect(self._read)
        with contextlib.suppress(Exception):
            source.stop()
        source.deleteLater()

        tail = self._buffer.flush()
        self._buffer = FrameBuffer()
        if tail:
            self.frameReady.emit(tail)
        self._format = None
        self.stopped.emit()

    # -- internals ----------------------------------------------------------

    def _read(self) -> None:
        io, audio_format = self._io, self._format
        if io is None or audio_format is None:
            return
        try:
            data = bytes(io.readAll().data())
        except Exception as exc:  # pragma: no cover - device disappeared
            logger.debug("Reading from the microphone failed: %s", exc)
            self.stop()
            return
        if not data:
            return
        audio = convert_to_frames(data, audio_format)
        loudness = _level(audio)
        self._peak = max(self._peak, loudness)
        self.level.emit(loudness)
        for frame in self._buffer.feed(audio):
            self.frameReady.emit(frame)
