"""Decryption via ffmpeg.

AAXC uses a per-file key + IV (from the license/voucher). Legacy AAX uses the
account-wide activation bytes. ffmpeg (>=4.4) supports both:

    ffmpeg -audible_key <hex> -audible_iv <hex> -i in.aaxc -c copy out.m4b   # AAXC
    ffmpeg -activation_bytes <hex>              -i in.aax  -c copy out.m4b   # AAX

``-c copy`` remuxes without re-encoding, so decryption is fast and lossless.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger("pipeline.decrypt")
settings = get_settings()


class DecryptionError(RuntimeError):
    pass


def _run_ffmpeg(args: list[str]) -> None:
    cmd = [settings.ffmpeg_path, "-y", "-hide_banner", "-loglevel", "error", *args]
    log.info("ffmpeg_run", stage="decrypt")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        # stderr may contain the key on some builds; log only a trimmed, generic message.
        raise DecryptionError(f"ffmpeg failed (exit {proc.returncode}): {proc.stderr.strip()[:500]}")


def _chapter_args(chapters_file: Path | None) -> list[str]:
    """When given a chapters ffmeta file, add it as a second input and map its chapters.

    We map only the audio stream (``0:a``): Audible files carry an internal *data*
    stream (codec 98314) the .m4b/ipod muxer rejects, and the cover art is re-embedded
    by the tagging step, so it need not be copied here. Chapters come from input 1.
    """
    if not chapters_file:
        return []
    return ["-i", str(chapters_file), "-map", "0:a", "-map_chapters", "1"]


def decrypt_aaxc(
    source: Path, dest: Path, *, key: str, iv: str, chapters_file: Path | None = None
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        ["-audible_key", key, "-audible_iv", iv, "-i", str(source)]
        + _chapter_args(chapters_file)
        + ["-c", "copy", str(dest)]
    )
    if not dest.exists() or dest.stat().st_size == 0:
        raise DecryptionError("Decryption produced no output")
    return dest


def decrypt_aax(
    source: Path, dest: Path, *, activation_bytes: str, chapters_file: Path | None = None
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        ["-activation_bytes", activation_bytes, "-i", str(source)]
        + _chapter_args(chapters_file)
        + ["-c", "copy", str(dest)]
    )
    if not dest.exists() or dest.stat().st_size == 0:
        raise DecryptionError("Decryption produced no output")
    return dest


def ffmpeg_available() -> bool:
    try:
        proc = subprocess.run(
            [settings.ffmpeg_path, "-version"], capture_output=True, text=True
        )
        return proc.returncode == 0
    except (OSError, FileNotFoundError):
        return False
