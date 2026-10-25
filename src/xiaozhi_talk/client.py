"""Async client around ``xiaozhi_sdk``.

This module is free of Qt imports and can be used from tests or any other
front end.  All state changes are reported through the :class:`Events`
callbacks; the bridge in :mod:`xiaozhi_talk.bridge` turns them into Qt signals.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import queue
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import websockets
from xiaozhi_sdk import XiaoZhiWebsocket
from xiaozhi_sdk.config import INPUT_AUDIO_SAMPLE_RATE

from .storage import DEFAULT_OTA_URL, Account
from .text import is_tool_call
from .voice import FRAME_MS, SILENCE_FRAME, TAIL_FRAMES

logger = logging.getLogger(__name__)

#: Sample rate of the decoded TTS audio in :attr:`ChatClient.audio_queue`.
PLAYBACK_SAMPLE_RATE = INPUT_AUDIO_SAMPLE_RATE

#: Seconds of silence after which the client tells the server it is listening.
#: The server ends idle sessions after about a minute; a real device resets that
#: timer by entering the listening state again, which is what this imitates.
KEEPALIVE_IDLE = 20.0

#: How often the keepalive checks whether the session needs a nudge.
KEEPALIVE_POLL = 5.0

#: How long to wait for the server's transcription after a recording.
VOICE_STT_TIMEOUT = 12.0


#: Hint appended when a server address points at the local machine.
LOCAL_SERVER_HINT = (
    " This address points to this computer, so a local Xiaozhi server has to be "
    "running there. Clear the WebSocket URL to use the official server instead."
)


def _is_local_address(url: str) -> bool:
    """True when ``url`` points at this machine."""
    host = (urlparse(url).hostname or "").lower()
    return host in {"localhost", "::1", "0.0.0.0"} or host.startswith("127.")


def _noop(*_args: Any, **_kwargs: Any) -> None:
    return None


@dataclass
class Events:
    """Callbacks invoked from the client's asyncio thread."""

    status: Callable[[str], None] = field(default=_noop)
    connected: Callable[[], None] = field(default=_noop)
    disconnected: Callable[[str], None] = field(default=_noop)
    user_text: Callable[[str], None] = field(default=_noop)
    reply_started: Callable[[], None] = field(default=_noop)
    reply_chunk: Callable[[str], None] = field(default=_noop)
    reply_finished: Callable[[], None] = field(default=_noop)
    emotion: Callable[[str], None] = field(default=_noop)
    notice: Callable[[str], None] = field(default=_noop)
    error: Callable[[str], None] = field(default=_noop)
    #: ``True`` once the server listens for a recording, ``False`` when the
    #: client refused to start one.
    speech_ready: Callable[[bool], None] = field(default=_noop)


class ChatSession(XiaoZhiWebsocket):
    """SDK session that keeps the MCP handshake alive.

    ``xiaozhi_sdk`` expects a ``vision`` capability in the MCP ``initialize``
    request and raises ``KeyError`` when a server does not offer one.  A failed
    handshake would leave the server waiting, so unsupported requests are
    answered with a minimal, valid reply instead.
    """

    #: Websocket close code and reason, filled in when the connection ends.
    close_code: int | None = None
    close_reason: str = ""

    async def close(self) -> None:
        """Close the socket before the reader task is cancelled.

        The SDK cancels its reader first, which leaves the websockets async
        iterator half closed and prints a traceback.  Closing the socket first
        lets the reader finish on its own.
        """
        socket = self.websocket
        if socket is not None:
            with contextlib.suppress(Exception):
                await socket.close()

        reader = self.message_handler_task
        if reader is not None and not reader.done():
            with contextlib.suppress(Exception):
                await asyncio.wait_for(reader, timeout=2.0)

        await super().close()

    async def mcp(self, data: dict[str, Any]) -> None:  # type: ignore[override]
        socket = self.websocket
        if socket is None or socket.close_code is not None:
            # The connection is going away; answering would only raise.
            return
        try:
            await super().mcp(data)
        except Exception as exc:
            logger.debug("MCP request failed (%s), answering minimally", exc)
            with contextlib.suppress(Exception):
                await self._answer_mcp_minimally(data)

    async def _message_handler(self) -> None:  # type: ignore[override]
        """Remember how the connection ended, then let the SDK clean up.

        ``ConnectionClosed`` is the normal way for a session to end, so it is
        logged at debug level instead of being reported as an error.
        """
        try:
            await super()._message_handler()
        except websockets.ConnectionClosed as exc:
            logger.debug("Connection closed while reading: %s", exc)
        finally:
            socket = self.websocket
            if socket is not None:
                self.close_code = socket.close_code
                self.close_reason = socket.close_reason or ""
                logger.debug(
                    "Connection ended with code %s (%s)", self.close_code, self.close_reason
                )

    async def keep_alive(self) -> bool:
        """Tell the server that this device is still listening.

        The server closes a session that stays quiet for about a minute.  A
        device keeps it open by going back into the listening state after every
        turn, so the same message is sent here.
        """
        socket = self.websocket
        if socket is None or socket.close_code is not None:
            return False
        message = {
            "session_id": self.session_id,
            "type": "listen",
            "state": "start",
            "mode": self.mode,
        }
        await socket.send(json.dumps(message))
        return True

    async def _answer_mcp_minimally(self, data: dict[str, Any]) -> None:
        payload = data.get("payload") or {}
        method = payload.get("method")
        if method == "initialize":
            self.mcp_initialize_payload["id"] = payload.get("id")
            await self.websocket.send(self.get_mcp_json(self.mcp_initialize_payload))
        elif method == "tools/list":
            self.mcp_tools_payload["id"] = payload.get("id")
            self.mcp_tools_payload["result"]["tools"] = []
            await self.websocket.send(self.get_mcp_json(self.mcp_tools_payload))
        else:
            logger.debug("Ignoring unsupported MCP method %s", method)


class ChatClient:
    """Owns one websocket session with a Xiaozhi server."""

    def __init__(
        self,
        account: Account,
        events: Events | None = None,
        *,
        keepalive_idle: float = KEEPALIVE_IDLE,
        keepalive_poll: float = KEEPALIVE_POLL,
        voice_stt_timeout: float = VOICE_STT_TIMEOUT,
    ) -> None:
        self.account = account
        self.events = events or Events()
        self._sdk: ChatSession | None = None
        self._disconnect_event: asyncio.Event | None = None
        self._last_sent_text = ""
        self._connected = False
        self._closing = False
        self._keepalive_idle = keepalive_idle
        self._keepalive_poll = keepalive_poll
        self._keepalive_task: asyncio.Task[None] | None = None
        self._last_activity = 0.0
        #: True from sending a message until the server stops speaking.  The
        #: keepalive stays quiet then: a "listening" message in the middle of a
        #: turn could make the server drop it.
        self._busy = False
        self._voice_stt_timeout = voice_stt_timeout
        #: Microphone frames waiting for the worker loop.  A queue because the
        #: capture thread fills it while the event loop drains it.
        self._voice_queue: queue.Queue[bytes] = queue.Queue()
        self._voice_active = False
        self._voice_task: asyncio.Task[None] | None = None
        #: A voice turn of ours is open: from a started recording until the
        #: reply to it ends.  A transcription that arrives in that window is
        #: the user's own speech and belongs in the chat.
        self._voice_turn = False
        #: The transcription of the open voice turn is already in the chat.
        self._voice_text_shown = False

        self._voice_stt_event: asyncio.Event | None = None

    # -- state --------------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def recording(self) -> bool:
        """True while microphone frames are being sent."""
        return self._voice_active

    @property
    def audio_queue(self) -> deque[Any] | None:
        """Decoded PCM frames produced by the SDK, or ``None``."""
        return self._sdk.output_audio_queue if self._sdk is not None else None

    def clear_audio(self) -> None:
        queue = self.audio_queue
        if queue is not None:
            queue.clear()

    # -- lifecycle ----------------------------------------------------------

    async def run(self) -> None:
        """Connect and stay alive until the session ends or is stopped."""
        if not await self.connect():
            return
        assert self._disconnect_event is not None
        try:
            await self._disconnect_event.wait()
        finally:
            await self.close()

    async def connect(self) -> bool:
        if self._sdk is not None:
            await self.close()

        websocket_url = self.account.websocket_url.strip() or None
        ota_url = None if websocket_url else (self.account.ota_url.strip() or DEFAULT_OTA_URL)
        target = websocket_url or ota_url or DEFAULT_OTA_URL

        session = ChatSession(self._handle_message, url=websocket_url, ota_url=ota_url)
        self._sdk = session
        self._disconnect_event = asyncio.Event()
        self.events.status("Connecting…")

        try:
            connected = await session.init_connection(
                self.account.mac_address,
                serial_number=self.account.serial_number,
                license_key=self.account.license_key,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Connection to %s failed", self.account.name)
            self.events.error(f"Could not reach {target}: {exc}{self._hint(target)}")
            self._sdk = None
            return False

        if not connected:
            self.events.error(
                f"Could not connect to {target}. Check the MAC address, the URL and "
                f"your network connection.{self._hint(target)}"
            )
            self._sdk = None
            return False

        self._connected = True
        self._last_activity = time.monotonic()
        self._keepalive_task = asyncio.create_task(self._keepalive())
        self.events.connected()
        if session.ota_task is not None:
            self.events.notice(
                "This device is not activated yet. Confirm it in the Xiaozhi console; "
                "the connection will be retried automatically."
            )
        return True

    @staticmethod
    def _hint(target: str) -> str:
        """Explain the most common mistake: a leftover local server address."""
        return LOCAL_SERVER_HINT if _is_local_address(target) else ""

    def request_stop(self) -> None:
        """Make :meth:`run` return; safe to call from another thread."""
        if self._disconnect_event is not None:
            self._disconnect_event.set()

    async def close(self) -> None:
        session, self._sdk = self._sdk, None
        was_connected = self._connected
        self._connected = False
        # While closing, the SDK still reports the socket shutdown; that is not
        # news worth telling the user about.
        self._closing = True
        if self._disconnect_event is not None:
            self._disconnect_event.set()
        self._busy = False
        self.clear_audio()
        await self.cancel_speech()
        await self._stop_keepalive()
        if session is not None:
            try:
                await session.close()
            except Exception as exc:  # pragma: no cover - shutdown best effort
                logger.debug("Closing the session failed: %s", exc)
        self._closing = False
        if was_connected:
            self.events.disconnected("Disconnected.")

    # -- keepalive ----------------------------------------------------------

    def _touch(self) -> None:
        """Remember that the session is busy, so no keepalive is needed."""
        self._last_activity = time.monotonic()

    async def _keepalive(self) -> None:
        """Keep an idle session from being closed by the server."""
        while self._connected:
            await asyncio.sleep(self._keepalive_poll)
            session = self._sdk
            if not self._connected or session is None:
                return
            if self._busy:
                continue
            if time.monotonic() - self._last_activity < self._keepalive_idle:
                continue
            try:
                sent = await session.keep_alive()
            except websockets.ConnectionClosed:
                return
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("Keepalive failed: %s", exc)
                return
            if not sent:
                return
            self._touch()
            logger.debug("Sent keepalive to the server")

    async def _stop_keepalive(self) -> None:
        task, self._keepalive_task = self._keepalive_task, None
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    # -- interaction --------------------------------------------------------

    async def send_text(self, text: str) -> bool:
        """Send a chat message and start a new reply."""
        session = self._sdk
        if session is None or session.websocket is None or not self._connected:
            self._mark_disconnected("Not connected. Use Session → Connect first.")
            return False
        try:
            ok = await session.send_text(text)
        except websockets.ConnectionClosed as exc:
            logger.debug("Sending failed, connection closed: %s", exc)
            self._mark_disconnected("The connection was closed while sending.")
            return False
        except Exception as exc:
            logger.exception("Sending a message failed")
            self.events.error(f"Sending the message failed: {exc}")
            return False
        if not ok:
            self._mark_disconnected("The server closed the connection.")
            return False
        self._last_sent_text = text.strip()
        self._busy = True
        self._touch()
        return True

    # -- voice input --------------------------------------------------------

    async def _listen_start(self) -> bool:
        """Ask the server to listen; report a broken connection here."""
        session = self._sdk
        if session is None:
            return False
        try:
            listening = await session.keep_alive()
        except websockets.ConnectionClosed as exc:
            logger.debug("Listening failed, connection closed: %s", exc)
            self._mark_disconnected("The connection was closed while sending.")
            return False
        except Exception as exc:
            logger.exception("Switching the server to listening failed")
            self.events.error(f"Voice input could not be started: {exc}")
            return False
        if not listening:
            self._mark_disconnected("The server closed the connection.")
            return False
        return True

    async def start_speech(self) -> bool:
        """Put the server into listening mode for a voice message."""
        session = self._sdk
        if session is None or not self._connected:
            self._mark_disconnected("Not connected. Use Session → Connect first.")
            self.events.speech_ready(False)
            return False
        if self._voice_active:
            return True
        if self._busy:
            self.events.notice("Wait until Xiaozhi has finished the current reply.")
            self.events.speech_ready(False)
            return False
        if not await self._listen_start():
            self.events.speech_ready(False)
            return False

        self._voice_queue = queue.Queue()
        self._voice_stt_event = asyncio.Event()
        self._voice_turn = True
        self._voice_text_shown = False
        self._voice_active = True
        # While the user speaks the session is busy: a keepalive in the middle
        # of the recording would interrupt it.
        self._busy = True
        self._touch()
        self._voice_task = asyncio.create_task(self._send_voice())
        self.events.speech_ready(True)
        return True

    def push_audio(self, frame: bytes) -> None:
        """Queue one microphone frame; safe to call from the GUI thread."""
        if not self._voice_active or not frame:
            return
        self._voice_queue.put(frame)

    async def _send_voice(self) -> None:
        """Send queued microphone frames as long as the recording lasts.

        Frames go out in real time, one per :data:`FRAME_MS`.  The tail that
        ends the recording is queued at once, and the server measures its
        length from the audio itself, so pacing it keeps the turn open exactly
        as long as the silence lasts.
        """
        session = self._sdk
        if session is None:
            return
        frame_seconds = FRAME_MS / 1000.0
        next_slot = time.monotonic()
        while True:
            try:
                frame = self._voice_queue.get_nowait()
            except queue.Empty:
                if not self._voice_active:
                    return
                await asyncio.sleep(0.01)
                continue
            now = time.monotonic()
            delay = next_slot - now
            if delay > 0:
                await asyncio.sleep(delay)
                now = time.monotonic()
            next_slot = max(next_slot + frame_seconds, now)
            try:
                await session.send_audio(frame)
            except websockets.ConnectionClosed:
                logger.debug("The voice stream ended with the connection")
                self._mark_disconnected(self._close_reason())
                return
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("Sending a voice frame failed: %s", exc)
                return
            self._touch()

    async def stop_speech(self) -> None:
        """Finish the recording and wait for the server to transcribe it."""
        if not self._voice_active:
            return
        self._voice_active = False
        # Without a quiet tail the server never decides that the user stopped
        # talking, so the recording would not be transcribed at all.
        for _ in range(TAIL_FRAMES):
            self._voice_queue.put(SILENCE_FRAME)

        task, self._voice_task = self._voice_task, None
        if task is not None:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(task, timeout=5.0)

        event, self._voice_stt_event = self._voice_stt_event, None
        if event is None or event.is_set():
            return
        try:
            await asyncio.wait_for(event.wait(), timeout=self._voice_stt_timeout)
        except asyncio.TimeoutError:
            # The turn stays open: a transcription that arrives late still ends
            # up in the chat instead of being dropped as foreign speech.
            self._busy = False
            self.events.notice(
                "Nothing was recognised. Speak a little closer to the microphone and try again."
            )

    async def end_speech(self) -> None:
        """Stop sending audio because the server ended the turn by itself.

        The server's voice detection sometimes finishes a turn while the user
        is still holding the recording.  Continuing would stream silence (or
        the reply's own audio) into a new turn, so the stream ends here.  The
        voice turn stays open: its transcription and reply still belong to it.
        """
        if not self._voice_active:
            return
        self._voice_active = False
        task, self._voice_task = self._voice_task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._voice_queue = queue.Queue()
        event = self._voice_stt_event
        if event is not None:
            event.set()

    async def cancel_speech(self) -> None:
        """Drop the current recording without waiting for a transcription."""
        # Only a real voice turn makes the session idle again; cancelling a
        # refused recording must not interrupt a reply that is still running.
        had_voice = self._voice_active or self._voice_turn
        self._voice_active = False
        self._voice_turn = False
        if had_voice:
            self._busy = False
        task, self._voice_task = self._voice_task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._voice_queue = queue.Queue()
        event, self._voice_stt_event = self._voice_stt_event, None
        if event is not None:
            event.set()

    def _mark_disconnected(self, reason: str) -> None:
        """Report a connection that is gone, at most once."""
        if self._closing:
            return
        if not self._connected:
            self.events.notice(reason)
            return
        self._connected = False
        self._voice_active = False
        if self._disconnect_event is not None:
            self._disconnect_event.set()
        event = self._voice_stt_event
        if event is not None:
            event.set()
        self.events.disconnected(reason)

    async def abort(self) -> None:
        """Interrupt the current reply."""
        await self.cancel_speech()
        self._busy = False
        self.clear_audio()
        session = self._sdk
        if session is None or session.websocket is None:
            return
        try:
            await session.send_abort()
        except Exception as exc:
            logger.debug("Abort failed: %s", exc)

    def _close_reason(self) -> str:
        """Describe how the server ended the connection."""
        session = self._sdk
        socket = getattr(session, "websocket", None) if session is not None else None
        code = getattr(socket, "close_code", None)
        reason = getattr(socket, "close_reason", "") or ""
        if code is None:
            return "The connection was closed."
        if reason:
            return f"The server closed the connection: {reason}."
        return f"The server closed the connection (code {code})."

    # -- incoming messages --------------------------------------------------

    async def _handle_message(self, message: dict[str, Any]) -> None:
        self._touch()
        message_type = message.get("type")
        state = message.get("state")

        if message_type == "stt":
            text = (message.get("text") or "").strip()
            if not text:
                return
            if text == self._last_sent_text:
                # The server echoes the text we typed; the UI already shows it.
                self._last_sent_text = ""
                return
            if self._voice_turn:
                if self._voice_text_shown:
                    logger.info("Ignoring a second transcription of the same turn: %s", text)
                    return
                # We recorded this ourselves, so the words are the user's.
                self._voice_text_shown = True
                self._busy = True
                event = self._voice_stt_event
                if event is not None:
                    event.set()
                logger.info("Transcribed the recording: %s", text)
                self.events.user_text(text)
                return
            # Anything else was recorded by the device itself. Showing it would
            # put words in the user's mouth, so it only goes to the log.
            logger.debug("Ignoring speech recognised elsewhere: %s", text)

        elif message_type == "tts":
            if state == "start":
                self.events.reply_started()
            elif state == "sentence_start":
                sentence = message.get("text") or ""
                if not is_tool_call(sentence):
                    self.events.reply_chunk(sentence)
            elif state == "stop":
                self._busy = False
                # The voice turn is over once its reply is: a transcription
                # that never arrived is not going to arrive now.
                self._voice_turn = False
                self._voice_text_shown = False
                # In hands-free mode the microphone stays quiet until the reply
                # has been played; the UI calls resume_listening() then.
                self.events.reply_finished()

        elif message_type == "llm":
            emotion = (message.get("text") or message.get("emotion") or "").strip()
            if emotion:
                self.events.emotion(emotion)

        elif message_type == "alert":
            detail = " ".join(
                part for part in (message.get("status"), message.get("message")) if part
            )
            self.events.notice(f"Server alert: {detail or 'unknown'}")

        elif message_type == "system":
            self.events.notice(f"Server command: {message.get('command', 'unknown')}")

        elif message_type == "websocket" and state == "close":
            self._mark_disconnected(self._close_reason())

        else:
            logger.debug("Unhandled message: %s", message)
