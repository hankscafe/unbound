"""AudiobookShelf (ABS) integration: resolve downloaded books to ABS library items.

Verified against ABS 2.35 API:
- ``GET /api/libraries`` -> ``{libraries: [{id, name, mediaType}]}`` (Bearer auth)
- ``GET /api/libraries/{id}/search?q=`` -> ``{book: [{libraryItem: {id, media:{metadata:{title, authorName, asin}}}}]}``
- web deep link to an item: ``{server}/item/{itemId}``

The API token is stored encrypted at rest. Matching prefers an exact ASIN match
(when ABS has one) and falls back to a normalized title (+author) match.
"""

from __future__ import annotations

import httpx
from sqlmodel import Session

from app.core.logging import get_logger
from app.core.runtime import get_secret_box
from app.db import init_db
from app.db.models import Book

log = get_logger("services.abs")


# --- config (token encrypted at rest) -------------------------------------


def get_config(session: Session) -> tuple[str | None, str | None, str | None]:
    """Return (base_url, token, library_id)."""
    base = init_db.get_setting(session, init_db.SETTING_ABS_URL)
    library_id = init_db.get_setting(session, init_db.SETTING_ABS_LIBRARY_ID)
    enc = init_db.get_setting(session, init_db.SETTING_ABS_TOKEN)
    token = None
    if enc:
        try:
            token = get_secret_box().decrypt_str(enc)
        except Exception:
            log.warning("abs_token_decrypt_failed")
    return (base.rstrip("/") if base else None), token, library_id


def set_token(session: Session, token: str | None) -> None:
    value = get_secret_box().encrypt(token.strip()) if token and token.strip() else None
    init_db.set_setting(session, init_db.SETTING_ABS_TOKEN, value)


def has_token(session: Session) -> bool:
    return bool(init_db.get_setting(session, init_db.SETTING_ABS_TOKEN))


# --- API client -----------------------------------------------------------


def _client(base: str, token: str, timeout: float = 10.0) -> httpx.Client:
    return httpx.Client(
        base_url=base,
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout,
        follow_redirects=True,
    )


def list_libraries(base: str, token: str) -> list[dict]:
    with _client(base, token) as c:
        r = c.get("/api/libraries")
        r.raise_for_status()
        return r.json().get("libraries", [])


def _norm(s: str | None) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def resolve_item_id(base: str, token: str, book: Book, library_id: str | None = None) -> str | None:
    """Find the ABS library item id for a book (ASIN match first, then title)."""
    book_lib_ids = [
        lib["id"] for lib in list_libraries(base, token) if lib.get("mediaType") == "book"
    ]
    # Honor a configured library only if it actually exists; otherwise auto-detect
    # (a stale/wrong id would otherwise silently match nothing).
    if library_id and library_id in book_lib_ids:
        lib_ids = [library_id]
    else:
        lib_ids = book_lib_ids
    title_n = _norm(book.title)
    author_n = _norm((book.authors or "").split(",")[0]) if book.authors else ""

    with _client(base, token) as c:
        for lid in lib_ids:
            try:
                r = c.get(f"/api/libraries/{lid}/search", params={"q": book.title, "limit": 10})
                r.raise_for_status()
            except httpx.HTTPError:
                continue
            matches = r.json().get("book", [])
            title_hit = None
            for m in matches:
                li = m.get("libraryItem") or {}
                md = (li.get("media") or {}).get("metadata") or {}
                item_id = li.get("id")
                if not item_id:
                    continue
                if book.asin and md.get("asin") and md["asin"] == book.asin:
                    return item_id  # exact ASIN match wins immediately
                if _norm(md.get("title")) == title_n:
                    md_author = _norm(md.get("authorName") or "")
                    # Prefer a title+author match; keep a title-only hit as fallback.
                    if not author_n or not md_author or author_n in md_author or md_author in author_n:
                        return item_id
                    title_hit = title_hit or item_id
            if title_hit:
                return title_hit
    return None


def item_url(base: str, item_id: str) -> str:
    return f"{base.rstrip('/')}/item/{item_id}"


def test_connection(base: str, token: str) -> dict:
    libs = list_libraries(base, token)
    return {
        "ok": True,
        "libraries": [
            {"id": lib["id"], "name": lib.get("name"), "mediaType": lib.get("mediaType")}
            for lib in libs
        ],
    }


def match_all(session: Session, rematch: bool = False) -> dict:
    """Resolve books to ABS item ids and persist them. Best-effort per book.

    Every book is matched (not just completed downloads) so titles that already
    live in AudiobookShelf — imported from anywhere — are recognized. When the
    ``abs_auto_exclude`` setting is on, matched books that we did NOT download
    are auto-excluded from (auto-)downloading, unless an admin re-included them
    before (``abs_exclude_override``).
    """
    from sqlmodel import select

    from app.db.models import Book, DownloadJob, EventLog, JobState

    base, token, library_id = get_config(session)
    if not base or not token:
        return {"configured": False, "checked": 0, "matched": 0, "auto_excluded": 0}

    completed = {
        j.book_id
        for j in session.exec(select(DownloadJob).where(DownloadJob.state == JobState.completed)).all()
    }
    auto_exclude = init_db.get_bool(session, init_db.SETTING_ABS_AUTO_EXCLUDE)
    checked = matched = auto_excluded = 0
    for book in session.exec(select(Book)).all():
        if book.abs_item_id and not rematch:
            continue
        checked += 1
        try:
            item_id = resolve_item_id(base, token, book, library_id)
        except Exception as exc:  # pragma: no cover - network
            log.warning("abs_match_error", asin=book.asin, error=str(exc))
            continue
        if item_id:
            book.abs_item_id = item_id
            session.add(book)
            matched += 1
            if (
                auto_exclude
                and book.id not in completed  # our own downloads are fine where they are
                and not book.excluded
                and not book.abs_exclude_override  # admin said "download it anyway"
            ):
                book.excluded = True
                book.abs_auto_excluded = True
                auto_excluded += 1
    if auto_excluded:
        session.add(EventLog(category="library",
                             message=f"Auto-excluded {auto_excluded} title(s) already in AudiobookShelf"))
    session.commit()
    log.info("abs_match_complete", checked=checked, matched=matched, auto_excluded=auto_excluded)
    return {"configured": True, "checked": checked, "matched": matched, "auto_excluded": auto_excluded}
