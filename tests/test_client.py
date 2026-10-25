"""End to end tests of the async client against a mock server."""

from __future__ import annotations

import asyncio

from mock_server import MockXiaozhiServer, wait_for_event
from xiaozhi_talk.client import ChatClient, Events
from xiaozhi_talk.storage import Account
from xiaozhi_talk.text import join_chunks
from xiaozhi_talk.voice import FRAME_BYTES, SILENCE_FRAME, TAIL_FRAMES


class Recorder:
    """Collects everything the client reports."""

    def __init__(self) -> None:
        self.statuses: list[str] = []
        self.connected = False
        self.disconnect_reason: str | None = None
        self.user_texts: list[str] = []
        self.emotions: list[str] = []
        self.chunks: list[str] = []
        self.notices: list[str] = []
        self.errors: list[str] = []
        self.speech: list[bool] = []
        self.started = False
        self.finished = asyncio.Event()

    @property
    def reply(self) -> str:
        text = ""
        for chunk in self.chunks:
            text = join_chunks(text, chunk)
        return text

    @property
    def events(self) -> Events:
        return Events(
            status=self.statuses.append,
            connected=self._on_connected,
            disconnected=self._on_disconnected,
            user_text=self.user_texts.append,
            reply_started=self._on_started,
            reply_chunk=self.chunks.append,
            reply_finished=self.finished.set,
            emotion=self.emotions.append,
            notice=self.notices.append,
            error=self.errors.append,
            speech_ready=self.speech.append,
        )

    def _on_connected(self) -> None:
        self.connected = True

    def _on_disconnected(self, reason: str) -> None:
        self.connected = False
        self.disconnect_reason = reason

    def _on_started(self) -> None:
        self.started = True


def _account(url: str) -> Account:
    return Account(
        name="Mock device",
        mac_address="00:11:22:33:44:55",
        ota_url="",
        websocket_url=url,
    )


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


def test_chat_round_trip() -> None:
    recorder, server = asyncio.run(_chat_scenario())

    assert recorder.connected is False  # closed at the end
    assert "Connecting…" in recorder.statuses
    assert recorder.started is True
    assert recorder.reply == "Hello there. How can I help you?"
    assert recorder.emotions == ["😊"]
    # The server echoes the typed text, the UI must not show it twice.
    assert recorder.user_texts == []
    assert recorder.errors == []

    # handshake carried the device identity
    device_id = server.headers[0].get("Device-Id")
    assert device_id == "00:11:22:33:44:55"
    assert server.headers[0].get("Protocol-Version") == "1"

    # the MCP initialize request is answered even without a vision capability
    assert server.mcp_replies, "the client must answer the MCP initialize request"
    result = server.mcp_replies[0]["payload"]["result"]
    assert result["protocolVersion"]


async def _chat_scenario() -> tuple[Recorder, MockXiaozhiServer]:
    recorder = Recorder()
    async with MockXiaozhiServer() as server:
        client = ChatClient(_account(server.url), recorder.events)
        assert await client.connect() is True
        assert client.connected is True

        assert await client.send_text("Hi Xiaozhi") is True
        await wait_for_event(recorder.finished)
        assert recorder.connected is True

        await client.close()
        assert client.connected is False
    return recorder, server


async def _keepalive_scenario(idle: float) -> tuple[int, bool]:
    recorder = Recorder()
    async with MockXiaozhiServer() as server:
        client = ChatClient(
            _account(server.url), recorder.events, keepalive_idle=idle, keepalive_poll=0.02
        )
        assert await client.connect() is True
        await asyncio.sleep(0.3)
        connected = client.connected
        await client.close()
        starts = sum(
            1
            for message in server.messages
            if message.get("type") == "listen" and message.get("state") == "start"
        )
        return starts, connected


def test_no_keepalive_while_a_turn_is_running() -> None:
    starts = asyncio.run(_busy_keepalive_scenario())

    assert starts == 1


async def _busy_keepalive_scenario() -> int:
    recorder = Recorder()
    async with MockXiaozhiServer() as server:
        client = ChatClient(
            _account(server.url), recorder.events, keepalive_idle=0.01, keepalive_poll=0.02
        )
        assert await client.connect() is True
        client._busy = True  # a reply is being produced
        await asyncio.sleep(0.2)
        await client.close()
        return sum(
            1
            for message in server.messages
            if message.get("type") == "listen" and message.get("state") == "start"
        )


def test_an_idle_session_is_kept_alive() -> None:
    starts, connected = asyncio.run(_keepalive_scenario(idle=0.05))

    assert connected is True
    assert starts >= 2  # the initial one plus at least one keepalive


def test_a_busy_session_is_not_nudged() -> None:
    starts, _connected = asyncio.run(_keepalive_scenario(idle=60.0))

    assert starts == 1


def test_abort_is_forwarded() -> None:
    server_messages = asyncio.run(_abort_scenario())
    assert any(message.get("type") == "abort" for message in server_messages)


async def _abort_scenario() -> list[dict]:
    recorder = Recorder()
    async with MockXiaozhiServer() as server:
        client = ChatClient(_account(server.url), recorder.events)
        await client.connect()
        await client.abort()
        await asyncio.sleep(0.1)
        await client.close()
        return server.messages


# ---------------------------------------------------------------------------
# voice input
# ---------------------------------------------------------------------------


def test_a_voice_message_is_transcribed_and_answered() -> None:
    frames, user_texts, reply, speech = asyncio.run(_voice_scenario())

    # ten recorded frames plus the silence that ends the turn; the SDK turns
    # them into Opus, so only the count is checked here
    assert len(frames) == 10 + TAIL_FRAMES
    assert all(frame for frame in frames)
    assert len(SILENCE_FRAME) == FRAME_BYTES
    assert user_texts == ["Hello from the microphone."]
    assert reply == "Hello there. How can I help you?"
    assert speech == [True]


async def _voice_scenario() -> tuple[list[bytes], list[str], str, list[bool]]:
    recorder = Recorder()
    async with MockXiaozhiServer() as server:
        client = ChatClient(_account(server.url), recorder.events)
        assert await client.connect() is True
        assert await client.start_speech() is True
        assert client.recording is True

        for _ in range(10):
            client.push_audio(b"\x01\x02" * 1440)

        await client.stop_speech()
        assert client.recording is False
        await wait_for_event(recorder.finished)
        await client.close()
    return server.audio_frames, recorder.user_texts, recorder.reply, recorder.speech


def test_the_microphone_opens_the_listening_state() -> None:
    starts = asyncio.run(_listening_scenario())
    # one from the handshake, one for the recording
    assert starts == 2


async def _listening_scenario() -> int:
    recorder = Recorder()
    async with MockXiaozhiServer(voice_after_frames=10**6) as server:
        client = ChatClient(_account(server.url), recorder.events, voice_stt_timeout=0.05)
        await client.connect()
        await client.start_speech()
        await client.cancel_speech()
        await client.close()
        return sum(
            1
            for message in server.messages
            if message.get("type") == "listen" and message.get("state") == "start"
        )


def test_a_recording_without_recognised_speech_is_reported() -> None:
    notices = asyncio.run(_silent_recording_scenario())

    assert notices
    assert "Nothing was recognised" in notices[0]


async def _silent_recording_scenario() -> list[str]:
    recorder = Recorder()
    async with MockXiaozhiServer(voice_after_frames=10**6) as server:
        client = ChatClient(_account(server.url), recorder.events, voice_stt_timeout=0.05)
        await client.connect()
        await client.start_speech()
        client.push_audio(b"\x00\x00" * 1440)
        await client.stop_speech()
        assert client._busy is False
        await client.close()
        return recorder.notices


def test_recording_is_refused_while_a_reply_runs() -> None:
    notices, speech = asyncio.run(_busy_recording_scenario())

    assert notices
    assert "finished the current reply" in notices[0]
    assert speech == [False]  # the UI has to take the microphone back


async def _busy_recording_scenario() -> tuple[list[str], list[bool]]:
    recorder = Recorder()
    async with MockXiaozhiServer() as server:
        client = ChatClient(_account(server.url), recorder.events)
        await client.connect()
        client._busy = True
        assert await client.start_speech() is False
        await client.close()
    return recorder.notices, recorder.speech


def test_frames_pushed_outside_a_recording_are_dropped() -> None:
    assert asyncio.run(_stray_frame_scenario()) == []


async def _stray_frame_scenario() -> list[bytes]:
    recorder = Recorder()
    async with MockXiaozhiServer(voice_after_frames=1) as server:
        client = ChatClient(_account(server.url), recorder.events)
        await client.connect()
        client.push_audio(b"\x01\x02" * 1440)
        await asyncio.sleep(0.2)
        await client.close()
        return server.audio_frames


def test_the_recording_ends_when_the_server_answers_first() -> None:
    user_texts, reply, frames = asyncio.run(_server_ended_turn_scenario())

    # The turn keeps running after the audio stream ended, so the words still
    # reach the chat and the answer is not cut off.
    assert user_texts == ["Hello from the microphone."]
    assert reply == "Hello there. How can I help you?"
    # No tail was added: the turn was already over.
    assert frames == 3


async def _server_ended_turn_scenario() -> tuple[list[str], str, int]:
    recorder = Recorder()
    async with MockXiaozhiServer() as server:
        client = ChatClient(_account(server.url), recorder.events)
        assert await client.connect() is True
        assert await client.start_speech() is True
        for _ in range(3):
            client.push_audio(b"\x01\x02" * 1440)
        await wait_for_event(recorder.finished)

        # The server detected the end of the turn before the user stopped.
        await client.end_speech()
        assert client.recording is False
        await client.close()
    return recorder.user_texts, recorder.reply, len(server.audio_frames)


def test_a_cancelled_recording_leaves_the_session_usable() -> None:
    asyncio.run(_cancel_scenario())


async def _cancel_scenario() -> None:
    recorder = Recorder()
    async with MockXiaozhiServer() as server:
        client = ChatClient(_account(server.url), recorder.events)
        await client.connect()
        assert await client.start_speech() is True
        await client.cancel_speech()

        assert client.recording is False
        assert client._busy is False
        assert await client.send_text("Hi Xiaozhi") is True
        await wait_for_event(recorder.finished)
        await client.close()


# ---------------------------------------------------------------------------
# failures
# ---------------------------------------------------------------------------


def test_connect_to_a_closed_port_reports_an_error() -> None:
    recorder = Recorder()
    account = _account("ws://127.0.0.1:1")
    client = ChatClient(account, recorder.events)

    assert asyncio.run(client.connect()) is False
    assert recorder.connected is False
    assert recorder.errors, "a failed connection must be reported"


def test_server_side_close_is_reported() -> None:
    recorder = asyncio.run(_close_scenario())
    assert recorder.disconnect_reason is not None or recorder.errors


async def _close_scenario() -> Recorder:
    recorder = Recorder()
    async with MockXiaozhiServer(close_after_hello=True) as server:
        client = ChatClient(_account(server.url), recorder.events)
        await client.connect()  # may fail or succeed, the close must show up
        await asyncio.sleep(0.3)
        await client.close()
    return recorder


def test_tool_call_sentences_are_not_shown_in_the_reply() -> None:
    recorder = Recorder()
    client = ChatClient(_account("ws://127.0.0.1:1"), recorder.events)

    asyncio.run(
        client._handle_message(
            {"type": "tts", "state": "sentence_start", "text": "% get_weather{}"}
        )
    )
    asyncio.run(
        client._handle_message({"type": "tts", "state": "sentence_start", "text": "It is warm."})
    )

    assert recorder.chunks == ["It is warm."]


def test_speech_recognised_elsewhere_is_not_shown_as_a_user_message() -> None:
    shown: list[str] = []
    client = ChatClient(_account("ws://127.0.0.1:1"), Events(user_text=shown.append))

    asyncio.run(client._handle_message({"type": "stt", "text": "ok"}))

    assert shown == []


def test_the_echo_of_a_typed_message_is_swallowed_once() -> None:
    shown: list[str] = []
    client = ChatClient(_account("ws://127.0.0.1:1"), Events(user_text=shown.append))
    client._last_sent_text = "hello"

    asyncio.run(client._handle_message({"type": "stt", "text": "hello"}))

    assert shown == []


def test_a_deliberate_close_is_not_reported_as_a_problem() -> None:
    seen: list[str] = []
    events = Events(notice=seen.append, disconnected=seen.append)
    client = ChatClient(_account("ws://127.0.0.1:1"), events)

    client._closing = True
    client._mark_disconnected("The connection was closed.")

    assert seen == []


def test_local_server_addresses_get_an_explanation() -> None:
    for url in ("ws://127.0.0.1:41811", "ws://localhost:8080", "ws://127.1.2.3:9000"):
        assert "local Xiaozhi server" in ChatClient._hint(url)


def test_remote_server_addresses_get_no_extra_hint() -> None:
    for url in ("wss://api.tenclass.net/xiaozhi/v1/", "ws://192.168.1.20:8000"):
        assert ChatClient._hint(url) == ""


def test_close_reason_includes_the_server_code() -> None:
    class FakeSocket:
        close_code = 1001
        close_reason = ""

    class FakeSession:
        websocket = FakeSocket()

    client = ChatClient(_account("ws://127.0.0.1:1"), Events())
    client._sdk = FakeSession()  # type: ignore[assignment]

    assert "1001" in client._close_reason()


def test_close_reason_without_a_code_is_generic() -> None:
    client = ChatClient(_account("ws://127.0.0.1:1"), Events())
    assert client._close_reason() == "The connection was closed."


def test_sending_without_a_connection_is_refused() -> None:
    recorder = Recorder()
    client = ChatClient(_account("ws://127.0.0.1:1"), recorder.events)

    assert asyncio.run(client.send_text("hello")) is False
    assert recorder.notices
