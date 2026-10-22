"""Minimal synchronous Server-Sent Events (SSE) parser for ``requests`` streams.

Yields ``{"event": str, "data": str, "json": dict|None}`` per SSE block,
mirroring the async parser used by the reference projects.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterator, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore


def _safe_json_loads(text: str) -> Optional[Dict[str, Any]]:
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    return None


def iter_sse(resp) -> Iterator[Dict[str, Any]]:
    """Iterate SSE blocks from a ``requests.Response`` opened with stream=True.

    Handles ``event:`` / ``data:`` lines, blank-line separators, comment lines
    and a possible tail block without a trailing blank line.
    """
    current_event = "message"
    current_data_lines: list[str] = []

    # SSE 规范默认字符集是 UTF-8；requests 对无 charset 的 text/* 会退化成
    # ISO-8859-1，导致中文被拆成单字节乱码。服务器没写 charset 时强制按 UTF-8 解。
    content_type = (getattr(resp, "headers", {}) or {}).get("Content-Type", "") or ""
    if "charset=" not in content_type.lower():
        resp.encoding = "utf-8"

    for raw_line in resp.iter_lines(decode_unicode=True):
        if raw_line is None:
            continue
        line = raw_line.rstrip("\r\n")

        if line == "":
            if current_data_lines:
                data_text = "\n".join(current_data_lines)
                yield {
                    "event": current_event,
                    "data": data_text,
                    "json": _safe_json_loads(data_text),
                }
            current_event = "message"
            current_data_lines = []
            continue

        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            current_event = line[6:].strip() or "message"
            continue
        if line.startswith("data:"):
            current_data_lines.append(line[5:].lstrip())
            continue

    # flush tail block
    if current_data_lines:
        data_text = "\n".join(current_data_lines)
        yield {
            "event": current_event,
            "data": data_text,
            "json": _safe_json_loads(data_text),
        }
