"""Bridge between the asyncio client and the Qt GUI thread.

:class:`ClientBridge` runs a :class:`~xiaozhi_talk.client.ChatClient` in a
background thread with its own event loop and forwards every state change as a
Qt signal, so widgets never touch asyncio objects directly.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections import deque
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Signal

from .client import ChatClient, Events
from .storage import Account

logger = logging.getLogger(__name__)


async def _drain_tasks() -> None:
    """Cancel and await every task that the SDK left running."""
    pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


class ClientBridge(QObject):
    """Owns the worker thread that talks to the Xiaozhi server."""

    statusChanged = Signal(str)
    connected = Signal()
    disconnected = Signal(str)
    userMessage = Signal(str)
    replyStarted = Signal()
    replyChunk = Signal(str)
    replyFinished = Signal()
    emotionChanged = Signal(str)
    notice = Signal(str)
    errorOccurred = Signal(str)
    speechReady = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: ChatClient | None = None
        self._ready = threading.Event()
        self._account: Account | None = None

    # -- public API ---------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def account(self) -> Account | None:
        return self._account

    @property
    def audio_queue(self) -> deque[Any] | None:
        client = self._client
        return client.audio_queue if client is not None else None

    def start(self, account: Account) -> None:
        """Connect to ``account`` in a fresh worker thread.

        ``_run`` starts :meth:`ChatClient.run` as the main task of the worker
        loop; starting it here as well would open a second websocket and leave
        the first one orphaned.
        """
        self.stop()
        self._account = account
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="xiaozhi-client", daemon=True)
        self._thread.start()
        if not self._ready.wait(5.0):  # pragma: no cover - thread start failure
            self.errorOccurred.emit("The network thread did not start.")

    def send_text(self, text: str) -> None:
        client = self._client
        if client is None or not client.connected:
            self.notice.emit("Not connected. Use Session → Connect first.")
            return
        self._submit(lambda: client.send_text(text))

    def start_listening(self) -> None:
        """Hands-free: stream the microphone until ``stop_listening``."""
        client = self._client
        if client is None or not client.connected:
            self.notice.emit("Not connected. Use Session → Connect first.")
            return
        self._submit(client.start_listening)

    def resume_listening(self) -> None:
        """Back to hands-free listening after the reply was played."""
        client = self._client
        if client is not None:
            self._submit(client.resume_listening)

    def stop_listening(self) -> None:
        client = self._client
        if client is not None:
            self._submit(client.stop_listening)

    def start_speech(self) -> None:
        """Ask the client to switch the server to listening for a recording."""
        client = self._client
        if client is None or not client.connected:
            self.notice.emit("Not connected. Use Session → Connect first.")
            return
        self._submit(client.start_speech)

    def push_audio(self, frame: bytes) -> None:
        """Hand one recorded frame to the client (called from the GUI thread)."""
        client = self._client
        if client is not None:
            client.push_audio(frame)

    def stop_speech(self) -> None:
        client = self._client
        if client is not None:
            self._submit(client.stop_speech)

    def end_speech(self) -> None:
        """Stop sending audio because the server already answered."""
        client = self._client
        if client is not None:
            self._submit(client.end_speech)

    def cancel_speech(self) -> None:
        client = self._client
        if client is not None:
            self._submit(client.cancel_speech)

    def abort(self) -> None:
        client = self._client
        if client is not None:
            self._submit(client.abort)

    def clear_audio(self) -> None:
        client = self._client
        if client is not None:
            client.clear_audio()

    def stop(self, wait: bool = True) -> None:
        """Ask the worker thread to finish and wait for it."""
        thread, loop, client = self._thread, self._loop, self._client
        self._thread = None
        if loop is not None and client is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(client.request_stop)
            except RuntimeError:  # loop already shutting down
                logger.debug("Event loop unavailable while stopping")
        if wait and thread is not None and thread.is_alive():
            thread.join(timeout=8.0)

    # -- worker thread ------------------------------------------------------

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        client = ChatClient(self._account, self._make_events())
        self._client = client
        self._ready.set()
        try:
            loop.run_until_complete(client.run())
        except asyncio.CancelledError:  # pragma: no cover - shutdown
            pass
        except Exception as exc:  # pragma: no cover - unexpected SDK failure
            logger.exception("Network thread crashed")
            self.errorOccurred.emit(f"The network thread failed: {exc}")
        finally:
            self._shutdown_loop(loop)
            self._loop = None
            self._client = None

    @staticmethod
    def _shutdown_loop(loop: asyncio.AbstractEventLoop) -> None:
        """Finish whatever the SDK left behind before closing ``loop``.

        Cancelling the session leaves helper tasks (keepalive, close handshake)
        pending for one iteration; closing the loop immediately would make them
        fail noisily during garbage collection.
        """
        with contextlib.suppress(Exception):  # pragma: no cover - best effort
            loop.run_until_complete(_drain_tasks())
        with contextlib.suppress(Exception):  # pragma: no cover - best effort
            loop.run_until_complete(loop.shutdown_asyncgens())
        with contextlib.suppress(Exception):  # pragma: no cover - best effort
            loop.run_until_complete(loop.shutdown_default_executor())
        loop.close()

    def _submit(self, coroutine_factory: Callable[[], Any]) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            logger.debug("Dropping call, event loop is gone")
            return
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine_factory(), loop)
        except RuntimeError:  # pragma: no cover - race during shutdown
            logger.debug("Dropping call, event loop is closing")
            return

        def _log(fut: Any) -> None:
            try:
                fut.result()
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("Background call failed: %s", exc)

        future.add_done_callback(_log)

    def _make_events(self) -> Events:
        return Events(
            status=self.statusChanged.emit,
            connected=self.connected.emit,
            disconnected=self.disconnected.emit,
            user_text=self.userMessage.emit,
            reply_started=self.replyStarted.emit,
            reply_chunk=self.replyChunk.emit,
            reply_finished=self.replyFinished.emit,
            emotion=self.emotionChanged.emit,
            notice=self.notice.emit,
            error=self.errorOccurred.emit,
            speech_ready=self.speechReady.emit,
        )
