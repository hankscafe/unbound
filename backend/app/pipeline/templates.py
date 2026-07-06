"""Filename/folder templating for library organization (Libation-style)."""

from __future__ import annotations

import re
from pathlib import Path

from app.db.models import Book, LibraryProfile

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Separator artifacts left dangling by empty template tokens (e.g. "{series_seq} - {title}"
# with no sequence -> "- Title"). Stripped from the ends of each path component.
_EDGE_SEP = re.compile(r"^[\s\-–—_]+|[\s\-–—_]+$")


def sanitize(component: str) -> str:
    """Make a single path component safe across Windows/Linux, trimming any
    separator artifacts left by empty template tokens."""
    cleaned = _INVALID.sub("_", component)
    cleaned = re.sub(r"\s+", " ", cleaned)  # collapse whitespace
    cleaned = _EDGE_SEP.sub("", cleaned).strip().strip(".")
    return cleaned or "Unknown"


def _fields(book: Book) -> dict[str, str]:
    year = ""
    if book.purchase_date:
        year = str(book.purchase_date.year)
    return {
        "author": book.authors or "Unknown Author",
        "title": book.title or "Untitled",
        "subtitle": book.subtitle or "",
        "narrator": book.narrators or "",
        "series": book.series or "",
        "series_seq": book.series_sequence or "",
        "asin": book.asin or "",
        "year": year,
    }


def _render(template: str, fields: dict[str, str]) -> str:
    def repl(match: re.Match[str]) -> str:
        return fields.get(match.group(1), "")

    rendered = re.sub(r"\{(\w+)\}", repl, template)
    # Collapse empty path segments (e.g. missing {series}) and sanitize each part.
    parts = [sanitize(p) for p in rendered.split("/") if p.strip()]
    return "/".join(parts)


def build_output_path(book: Book, profile: LibraryProfile) -> Path:
    fields = _fields(book)
    folder = _render(profile.folder_template, fields)
    filename = _render(profile.filename_template, fields) or sanitize(book.title)
    ext = profile.audio_format.value if hasattr(profile.audio_format, "value") else str(
        profile.audio_format
    )
    root = Path(profile.root_path)
    return root / folder / f"{filename}.{ext}"
