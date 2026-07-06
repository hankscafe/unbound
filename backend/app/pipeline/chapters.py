"""Build an ffmetadata chapters file for embedding into the .m4b via ffmpeg."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# ffmetadata requires these to be backslash-escaped in values.
_ESCAPE = re.compile(r"([=;#\\\n])")


def _escape(value: str) -> str:
    return _ESCAPE.sub(r"\\\1", value)


def build_ffmeta(chapters: list[dict[str, Any]]) -> str:
    """Render chapters (each {title, start_ms, length_ms}) as an ffmetadata document."""
    lines = [";FFMETADATA1"]
    for c in chapters:
        start = int(c["start_ms"])
        end = start + int(c["length_ms"])
        lines += [
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={start}",
            f"END={end}",
            f"title={_escape(str(c.get('title') or 'Chapter'))}",
        ]
    return "\n".join(lines) + "\n"


def write_ffmeta(chapters: list[dict[str, Any]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_ffmeta(chapters), encoding="utf-8")
    return path
