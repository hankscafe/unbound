"""Move the finished file into the configured library, applying the naming template."""

from __future__ import annotations

import shutil
from pathlib import Path

from app.db.models import Book, LibraryProfile
from app.pipeline.templates import build_output_path


def move_to_library(source: Path, book: Book, profile: LibraryProfile) -> Path:
    dest = build_output_path(book, profile)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    shutil.move(str(source), str(dest))
    return dest
