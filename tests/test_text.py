"""Tests for the text helpers."""

from __future__ import annotations

import pytest

from xiaozhi_talk.text import format_mac, is_tool_call, join_chunks


@pytest.mark.parametrize(
    ("current", "chunk", "expected"),
    [
        ("", "", ""),
        ("", "Hello", "Hello"),
        ("Hello", "", "Hello"),
        ("Hello", "world.", "Hello world."),
        ("你好。", "今天天气不错。", "你好。今天天气不错。"),
        ("你好", "hello", "你好hello"),
        ("Done.", ", really", "Done., really"),
        ("Line", "\nNext", "Line\nNext"),
        ("Ends ", "with space", "Ends with space"),
    ],
)
def test_join_chunks(current: str, chunk: str, expected: str) -> None:
    assert join_chunks(current, chunk) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("00:11:22:33:44:55", "00:11:22:33:44:55"),
        ("AA:BB:CC:DD:EE:FF", "aa:bb:cc:dd:ee:ff"),
        ("aabbccddeeff", "aa:bb:cc:dd:ee:ff"),
        ("00-11-22-33-44-55", "00:11:22:33:44:55"),
        (" 00:11:22:33:44:55 ", "00:11:22:33:44:55"),
        ("too-short", "too-short"),
        ("", ""),
    ],
)
def test_format_mac(raw: str, expected: str) -> None:
    assert format_mac(raw) == expected


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        ("% get_weather{}", True),
        ("   %get_weather", True),
        ("It is 21 degrees.", False),
        ("100% sure", False),
        ("", False),
    ],
)
def test_is_tool_call(sentence: str, expected: bool) -> None:
    assert is_tool_call(sentence) is expected
