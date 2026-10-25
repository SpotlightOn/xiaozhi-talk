"""A tiny in-process Xiaozhi server for the tests.

It speaks just enough of the websocket protocol to drive a chat session:
hello handshake, MCP initialize, a text reply streamed as TTS sentences, and
voice input: the first few binary frames are treated as a finished recording
and answered with a transcription.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import websockets

logger = logging.getLogger(__name__)

DEFAULT_SENTENCES = ["Hello there.", "How can I help you?"]


class MockXiaozhiServer:
    def __init__(
        self,
        sentences: list[str] | None = None,
        *,
        offer_vision: bool = False,
        close_after_hello: bool = False,
        voice_text: str = "Hello from the microphone.",
        voice_after_frames: int = 3,
    ) -> None:
        self.sentences = list(sentences or DEFAULT_SENTENCES)
        self.offer_vision = offer_vision
        self.close_after_hello = close_after_hello
        self.voice_text = voice_text
        self.voice_after_frames = voice_after_frames
        self.audio_frames: list[bytes] = []
        self._voice_answered = False

        self.messages: list[dict[str, Any]] = []
        self.mcp_replies: list[dict[str, Any]] = []
        self.headers: list[Any] = []
        self.connections = 0
        self.session_id = "mock-session"

        self._server: Any | None = None
        self._connections: set[Any] = set()
        self._port = 0

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> MockXiaozhiServer:
        self._server = await websockets.serve(self._handle_connection, "127.0.0.1", 0)
        self._port = self._server.sockets[0].getsockname()[1]
        return self

    async def stop(self) -> None:
        server, self._server = self._server, None
        if server is None:
            return
        server.close()
        # Close open client sockets so their handlers can run to completion;
        # otherwise wait_closed() blocks until the client is collected.
        for websocket in list(self._connections):
            await websocket.close()
        try:
            await asyncio.wait_for(server.wait_closed(), timeout=5.0)
        except TimeoutError:  # pragma: no cover - only on a stuck client
            logger.debug("Mock server did not close all connections in time")

    async def __aenter__(self) -> MockXiaozhiServer:
        return await self.start()

    async def __aexit__(self, *_exc_info: Any) -> None:
        await self.stop()

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self._port}"

    # -- protocol -----------------------------------------------------------

    async def _handle_connection(self, websocket: Any) -> None:
        self.connections += 1
        self.headers.append(websocket.request.headers)
        self._connections.add(websocket)
        try:
            async for raw in websocket:
                if isinstance(raw, (bytes, bytearray, memoryview)):
                    await self._handle_audio(websocket, bytes(raw))
                    continue
                message = json.loads(raw)
                self.messages.append(message)
                await self._dispatch(websocket, message)
        except websockets.ConnectionClosed:
            logger.debug("Mock server connection closed")
        finally:
            self._connections.discard(websocket)

    async def _dispatch(self, websocket: Any, message: dict[str, Any]) -> None:
        kind = message.get("type")

        if kind == "hello":
            await websocket.send(
                json.dumps(
                    {
                        "type": "hello",
                        "transport": "websocket",
                        "session_id": self.session_id,
                        "audio_params": {
                            "format": "opus",
                            "sample_rate": 24000,
                            "channels": 1,
                            "frame_duration": 60,
                        },
                    }
                )
            )
            if self.close_after_hello:
                await websocket.close()
                return
            await self._send_mcp_initialize(websocket)

        elif kind == "mcp":
            self.mcp_replies.append(message)

        elif kind == "listen" and message.get("state") == "detect":
            await self._stream_reply(websocket, message.get("text", ""))

        elif kind == "abort":
            await websocket.send(
                json.dumps({"type": "tts", "state": "stop", "session_id": self.session_id})
            )

    async def _handle_audio(self, websocket: Any, frame: bytes) -> None:
        """Collect microphone frames and transcribe them once there are enough."""
        self.audio_frames.append(frame)
        if self._voice_answered or len(self.audio_frames) < self.voice_after_frames:
            return
        self._voice_answered = True
        await self._stream_reply(websocket, self.voice_text)

    async def _send_mcp_initialize(self, websocket: Any) -> None:
        params: dict[str, Any] = {"capabilities": {}}
        if self.offer_vision:
            params["capabilities"]["vision"] = {
                "url": "http://127.0.0.1/vision",
                "token": "vision-token",
            }
        await websocket.send(
            json.dumps(
                {
                    "type": "mcp",
                    "session_id": self.session_id,
                    "payload": {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": params,
                    },
                }
            )
        )

    async def _stream_reply(self, websocket: Any, user_text: str) -> None:
        await websocket.send(
            json.dumps({"type": "stt", "session_id": self.session_id, "text": user_text})
        )
        await websocket.send(
            json.dumps(
                {
                    "type": "llm",
                    "session_id": self.session_id,
                    "emotion": "happy",
                    "text": "😊",
                }
            )
        )
        await websocket.send(
            json.dumps({"type": "tts", "state": "start", "session_id": self.session_id})
        )
        for sentence in self.sentences:
            await websocket.send(
                json.dumps(
                    {
                        "type": "tts",
                        "state": "sentence_start",
                        "session_id": self.session_id,
                        "text": sentence,
                    }
                )
            )
            await asyncio.sleep(0)
        await websocket.send(
            json.dumps({"type": "tts", "state": "stop", "session_id": self.session_id})
        )


async def wait_for_event(event: asyncio.Event, timeout: float = 5.0) -> None:
    """Wait for ``event`` or fail the test after ``timeout`` seconds."""
    await asyncio.wait_for(asyncio.shield(event.wait()), timeout)
