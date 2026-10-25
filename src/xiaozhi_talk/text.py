"""Helpers for assembling streamed reply text."""

from __future__ import annotations

import re

_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]")
_NO_SPACE_BEFORE = set(",.;:!?)]}%'\"’”…。，、；：！？）】》」』")


def join_chunks(current: str, chunk: str) -> str:
    """Append ``chunk`` to ``current`` with natural spacing.

    Xiaozhi servers stream a reply sentence by sentence.  Chinese sentences
    must be concatenated directly, English ones need a separating space.
    """
    if not chunk:
        return current
    if not current:
        return chunk
    if current[-1].isspace() or chunk[0].isspace():
        return current + chunk
    if _CJK.match(current[-1]) or _CJK.match(chunk[0]):
        return current + chunk
    if chunk[0] in _NO_SPACE_BEFORE:
        return current + chunk
    return current + " " + chunk


def is_tool_call(sentence: str) -> bool:
    """Return True for the pseudo sentences that announce an MCP tool call.

    Xiaozhi servers send ``% tool_name{...}`` as a TTS sentence before running a
    tool. Devices recognise the leading ``%`` and do not speak it, so the chat
    should not show it either.
    """
    return sentence.lstrip().startswith("%")


def format_mac(value: str) -> str:
    """Normalise user input like ``00-11-22-33-44-55`` to the SDK format."""
    digits = re.sub(r"[^0-9A-Fa-f]", "", value or "")
    if len(digits) != 12:
        return (value or "").strip()
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2)).lower()
