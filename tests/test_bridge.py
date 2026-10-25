"""Tests for the worker thread's lifecycle."""

from __future__ import annotations

import asyncio
import threading
import time

from mock_server import MockXiaozhiServer
from xiaozhi_talk.bridge import ClientBridge, _drain_tasks
from xiaozhi_talk.storage import Account


def test_drain_tasks_cancels_every_child_task() -> None:
    async def background() -> None:
        await asyncio.sleep(3600)

    async def scenario() -> asyncio.Task[None]:
        task = asyncio.create_task(background())
        await asyncio.sleep(0)  # let the task start
        await _drain_tasks()
        return task

    task = asyncio.run(scenario())
    assert task.cancelled()


def test_shutdown_loop_finishes_tasks_and_async_generators(capfd) -> None:
    async def generator():
        yield 1
        yield 2

    async def background() -> None:
        await asyncio.sleep(3600)

    async def prepare() -> None:
        asyncio.create_task(background())
        started = generator()
        await anext(started)  # leave the generator suspended
        await asyncio.sleep(0)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(prepare())

    ClientBridge._shutdown_loop(loop)

    assert loop.is_closed()
    _out, err = capfd.readouterr()
    assert "Task was destroyed" not in err
    assert "asynchronous generator" not in err


def _start_server() -> tuple[MockXiaozhiServer, asyncio.AbstractEventLoop]:
    server = MockXiaozhiServer()
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    asyncio.run_coroutine_threadsafe(server.start(), loop).result(10)
    return server, loop


def _stop_server(server: MockXiaozhiServer, loop: asyncio.AbstractEventLoop) -> None:
    try:
        asyncio.run_coroutine_threadsafe(server.stop(), loop).result(20)
    finally:
        loop.call_soon_threadsafe(loop.stop)


def test_start_opens_exactly_one_connection(qapp) -> None:
    server, loop = _start_server()
    bridge = ClientBridge()
    connected: list[bool] = []
    bridge.connected.connect(lambda: connected.append(True))
    account = Account(
        name="Mock",
        mac_address="00:11:22:33:44:55",
        ota_url="",
        websocket_url=server.url,
    )
    try:
        bridge.start(account)

        deadline = time.monotonic() + 5.0
        while not connected and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.02)

        assert connected, "the bridge did not report a connection"
        assert server.connections == 1
    finally:
        bridge.stop()
        _stop_server(server, loop)
