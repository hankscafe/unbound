"""Embed metadata and cover art into the decrypted .m4b (mutagen).

Chapters embedding is left to ffmpeg during the decrypt/remux step (from the Audible
chapter_info); this module handles the descriptive tags and cover the app already knows.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from app.core.logging import get_logger
from app.db.models import Book

log = get_logger("pipeline.tagging")


def _fetch_cover(url: str) -> bytes | None:
    try:
        resp = httpx.get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        return resp.content
    except Exception as exc:  # pragma: no cover - network
        log.warning("cover_fetch_failed", error=str(exc))
        return None


def apply_tags(path: Path, book: Book, *, embed_cover: bool = True) -> None:
    try:
        from mutagen.mp4 import MP4, MP4Cover
    except Exception as exc:  # pragma: no cover
        log.warning("mutagen_unavailable", error=str(exc))
        return

    audio = MP4(str(path))
    audio["\xa9nam"] = book.title
    if book.authors:
        audio["\xa9ART"] = book.authors
        audio["aART"] = book.authors
    if book.narrators:
        audio["\xa9wrt"] = book.narrators
    if book.series:
        audio["\xa9alb"] = book.series
        audio["----:com.apple.iTunes:SERIES"] = book.series.encode("utf-8")  # type: ignore[assignment]
    if book.series_sequence:
        audio["----:com.apple.iTunes:SERIES-PART"] = book.series_sequence.encode("utf-8")  # type: ignore[assignment]
    audio["stik"] = [2]  # Audiobook media type

    if embed_cover and book.cover_url:
        data = _fetch_cover(book.cover_url)
        if data:
            fmt = MP4Cover.FORMAT_PNG if data[:4] == b"\x89PNG" else MP4Cover.FORMAT_JPEG
            audio["covr"] = [MP4Cover(data, imageformat=fmt)]

    audio.save()
    log.info("tags_applied", asin=book.asin)
