"""Thin, well-typed wrapper over the ``audible`` library.

Responsibilities:
- (de)serialize an :class:`audible.Authenticator` to/from an opaque JSON blob that the
  app encrypts at rest (never persisted in the clear).
- pull the account library (paged) and normalize items.
- fetch decryption key material: per-file license/voucher (AAXC) or activation bytes (AAX).

Network calls require the user's own linked account. The ``audible`` import is guarded so
the app still boots (and non-Audible features work) if the dependency is missing.
"""

from __future__ import annotations

import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

log = get_logger("audible.client")

try:  # pragma: no cover - import guard
    import audible  # type: ignore

    AUDIBLE_AVAILABLE = True
except Exception:  # pragma: no cover
    audible = None  # type: ignore
    AUDIBLE_AVAILABLE = False


class AudibleUnavailable(RuntimeError):
    """Raised when the ``audible`` dependency is not importable."""


class LicenseError(RuntimeError):
    """Raised when Audible denies a license/download request for a title."""


def _require_audible() -> None:
    if not AUDIBLE_AVAILABLE:
        raise AudibleUnavailable(
            "The 'audible' library is not installed in this environment."
        )


# --- Serialization ---------------------------------------------------------


def serialize_auth(auth: "audible.Authenticator") -> str:  # type: ignore[name-defined]
    """Serialize an authenticator to a JSON string (to be encrypted by the caller).

    Uses the library's own file writer into a temp file, then reads it back, so we
    stay compatible across ``audible`` versions. The temp file is unencrypted only
    for the microseconds before deletion; the durable copy is always encrypted by
    :class:`app.core.security.SecretBox`.
    """
    _require_audible()
    with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as tf:
        tmp = Path(tf.name)
    try:
        auth.to_file(tmp, encryption=False)
        return tmp.read_text(encoding="utf-8")
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def deserialize_auth(blob: str) -> "audible.Authenticator":  # type: ignore[name-defined]
    _require_audible()
    with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as tf:
        tf.write(blob)
        tmp = Path(tf.name)
    try:
        return audible.Authenticator.from_file(tmp)  # type: ignore[union-attr]
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


# --- Normalized library item ----------------------------------------------


@dataclass
class LibraryItem:
    asin: str
    title: str
    subtitle: str | None
    authors: str | None
    narrators: str | None
    series: str | None
    series_sequence: str | None
    runtime_minutes: int | None
    cover_url: str | None
    purchase_date: str | None
    is_aax_available: bool
    raw: dict[str, Any]


def _join_names(items: Any, key: str = "name") -> str | None:
    if not items:
        return None
    names = [i.get(key) for i in items if isinstance(i, dict) and i.get(key)]
    return ", ".join(names) if names else None


def _normalize_item(item: dict[str, Any]) -> LibraryItem:
    series = item.get("series") or []
    first_series = series[0] if series else {}
    codecs = item.get("available_codecs") or []
    codec_names = {c.get("name", "").upper() for c in codecs if isinstance(c, dict)}
    is_aax = any("AAX" == n or n.startswith("AAX_") for n in codec_names)
    images = item.get("product_images") or {}
    cover = None
    if isinstance(images, dict) and images:
        # pick the largest available key
        cover = images.get("500") or next(iter(images.values()), None)
    return LibraryItem(
        asin=item.get("asin", ""),
        title=item.get("title", "Untitled"),
        subtitle=item.get("subtitle"),
        authors=_join_names(item.get("authors")),
        narrators=_join_names(item.get("narrators")),
        series=first_series.get("title") if isinstance(first_series, dict) else None,
        series_sequence=first_series.get("sequence") if isinstance(first_series, dict) else None,
        runtime_minutes=item.get("runtime_length_min"),
        cover_url=cover,
        purchase_date=item.get("purchase_date"),
        is_aax_available=is_aax,
        raw=item,
    )


# --- Store: catalog search & wishlist ---------------------------------------


@dataclass
class StoreItem:
    """A catalog/wishlist product (not necessarily owned)."""

    asin: str
    title: str
    subtitle: str | None
    authors: str | None
    narrators: str | None
    series: str | None
    series_sequence: str | None
    runtime_minutes: int | None
    cover_url: str | None
    price_display: str | None
    release_date: str | None
    raw: dict[str, Any]


_STORE_RESPONSE_GROUPS = "contributors, media, price, product_attrs, product_desc, series"


def _price_display(price: Any) -> str | None:
    """Human display of the catalog price block (best-effort; shapes vary)."""
    if not isinstance(price, dict):
        return None
    block = price.get("lowest_price") or price.get("list_price") or {}
    base = block.get("base")
    if base is None:
        return None
    currency = block.get("currency_code") or ""
    return f"{base} {currency}".strip()


def _normalize_product(item: dict[str, Any]) -> StoreItem:
    base = _normalize_item(item)
    return StoreItem(
        asin=base.asin,
        title=base.title,
        subtitle=base.subtitle,
        authors=base.authors,
        narrators=base.narrators,
        series=base.series,
        series_sequence=base.series_sequence,
        runtime_minutes=base.runtime_minutes,
        cover_url=base.cover_url,
        price_display=_price_display(item.get("price")),
        release_date=item.get("release_date"),
        raw=item,
    )


def search_catalog(
    auth: "audible.Authenticator",  # type: ignore[name-defined]
    keywords: str,
    page: int = 0,
    num_results: int = 20,
) -> tuple[list[StoreItem], int]:
    """Search the Audible catalog (marketplace comes from the account's auth)."""
    _require_audible()
    with audible.Client(auth=auth) as client:  # type: ignore[union-attr]
        resp = client.get(
            "1.0/catalog/products",
            keywords=keywords,
            num_results=min(num_results, 50),
            page=page,
            products_sort_by="Relevance",
            response_groups=_STORE_RESPONSE_GROUPS,
        )
    products = resp.get("products", []) if isinstance(resp, dict) else []
    total = int(resp.get("total_results") or len(products)) if isinstance(resp, dict) else 0
    return [_normalize_product(p) for p in products], total


def get_wishlist(auth: "audible.Authenticator") -> list[StoreItem]:  # type: ignore[name-defined]
    """The account's full wishlist (paged)."""
    _require_audible()
    items: list[StoreItem] = []
    with audible.Client(auth=auth) as client:  # type: ignore[union-attr]
        page = 0
        while True:
            resp = client.get(
                "1.0/wishlist",
                num_results=50,
                page=page,
                response_groups=_STORE_RESPONSE_GROUPS,
            )
            batch = resp.get("products", []) if isinstance(resp, dict) else []
            if not batch:
                break
            items.extend(_normalize_product(p) for p in batch)
            if len(batch) < 50:
                break
            page += 1
    return items


def add_to_wishlist(auth: "audible.Authenticator", asin: str) -> None:  # type: ignore[name-defined]
    _require_audible()
    with audible.Client(auth=auth) as client:  # type: ignore[union-attr]
        client.post("1.0/wishlist", body={"asin": asin})


def remove_from_wishlist(auth: "audible.Authenticator", asin: str) -> None:  # type: ignore[name-defined]
    _require_audible()
    with audible.Client(auth=auth) as client:  # type: ignore[union-attr]
        client.delete(f"1.0/wishlist/{asin}")


def purchase_with_credit(auth: "audible.Authenticator", asin: str) -> dict[str, Any]:
    """Buy a title using ONE available Audible credit — never the payment card.

    ``audiblecreditapplied=true`` draws from the account's credit balance
    (monthly membership credits included). Zero credits → Audible rejects the
    order; we deliberately never fall back to the default payment method.
    """
    _require_audible()
    with audible.Client(auth=auth) as client:  # type: ignore[union-attr]
        resp = client.post(
            "1.0/orders", body={"asin": asin, "audiblecreditapplied": "true"}
        )
    log.info("credit_purchase", asin=asin)
    return resp if isinstance(resp, dict) else {}


# --- Library sync & licensing ---------------------------------------------

# NB: "available_codecs" is NOT a valid *requested* library response group (Audible
# returns 400), but the field is included in the payload anyway via "media" — so we
# still detect AAX availability from it in _normalize_item without requesting it.
_RESPONSE_GROUPS = (
    "contributors, media, product_desc, product_attrs, series, "
    "product_extended_attrs, customer_rights"
)


def fetch_library(auth: "audible.Authenticator") -> list[LibraryItem]:  # type: ignore[name-defined]
    """Return all owned items for the account behind ``auth``."""
    _require_audible()
    items: list[LibraryItem] = []
    with audible.Client(auth=auth) as client:  # type: ignore[union-attr]
        page = 1
        while True:
            resp = client.get(
                "1.0/library",
                num_results=1000,
                page=page,
                response_groups=_RESPONSE_GROUPS,
                sort_by="-PurchaseDate",
            )
            batch = resp.get("items", []) if isinstance(resp, dict) else []
            if not batch:
                break
            items.extend(_normalize_item(i) for i in batch)
            if len(batch) < 1000:
                break
            page += 1
    log.info("library_fetched", count=len(items))
    return items


def get_activation_bytes(auth: "audible.Authenticator") -> str:  # type: ignore[name-defined]
    """Account-wide activation bytes for legacy AAX decryption."""
    _require_audible()
    return auth.get_activation_bytes(extract=True)  # type: ignore[union-attr]


def get_chapters(
    auth: "audible.Authenticator", asin: str  # type: ignore[name-defined]
) -> list[dict[str, Any]] | None:
    """Fetch flat chapter markers for a title: [{title, start_ms, length_ms}, ...].

    Returns None when Audible has no chapter data for the item.
    """
    _require_audible()
    with audible.Client(auth=auth) as client:  # type: ignore[union-attr]
        resp = client.get(
            f"1.0/content/{asin}/metadata",
            response_groups="chapter_info",
            chapter_titles_type="Flat",
        )
    ci = (resp.get("content_metadata") or {}).get("chapter_info") if isinstance(resp, dict) else None
    if not ci:
        return None
    out: list[dict[str, Any]] = []
    for c in ci.get("chapters") or []:
        out.append(
            {
                "title": c.get("title") or f"Chapter {len(out) + 1}",
                "start_ms": int(c.get("start_offset_ms", 0)),
                "length_ms": int(c.get("length_ms", 0)),
            }
        )
    return out or None


@dataclass
class DownloadLicense:
    """Per-file decryption material for AAXC plus the download URL."""

    download_url: str
    key: str | None
    iv: str | None
    format: str  # aaxc | aax
    raw: dict[str, Any]


# Headers Audible expects on a license request (mirrors audible-cli).
_LICENSE_HEADERS = {
    "X-ADP-SW": "37801821",
    "X-ADP-Transport": "WIFI",
    "X-ADP-LTO": "120",
    "X-Device-Type-Id": "A2CZJZGLK2JJVM",
    "device_idiom": "phone",
}


def get_aaxc_license(
    auth: "audible.Authenticator", asin: str  # type: ignore[name-defined]
) -> DownloadLicense:
    """Request the AAXC license for a book and return the *decrypted* key+iv + URL.

    The ``license_response`` in the API reply is an encrypted voucher; the actual
    decryption key and IV are recovered with the account's device data via
    :func:`audible.aescipher.decrypt_voucher_from_licenserequest`.
    """
    _require_audible()
    from audible.aescipher import decrypt_voucher_from_licenserequest  # type: ignore

    headers = {"X-Amzn-RequestId": secrets.token_hex(20).upper(), **_LICENSE_HEADERS}
    body = {
        "supported_drm_types": ["Mpeg", "Adrm"],
        "quality": "High",
        "consumption_type": "Download",
        "response_groups": "last_position_heard, pdf_url, content_reference, chapter_info",
    }
    with audible.Client(auth=auth) as client:  # type: ignore[union-attr]
        resp = client.post(f"1.0/content/{asin}/licenserequest", body=body, headers=headers)

    cl = (resp.get("content_license") or {}) if isinstance(resp, dict) else {}
    if cl.get("status_code") == "Denied":
        raise LicenseError(cl.get("message") or f"License denied for {asin}")

    meta = cl.get("content_metadata") or {}
    dl_url = (meta.get("content_url") or {}).get("offline_url", "")
    codec = (meta.get("content_reference") or {}).get("content_format", "aaxc")

    voucher = decrypt_voucher_from_licenserequest(auth, resp)
    return DownloadLicense(
        download_url=dl_url,
        key=voucher.get("key"),
        iv=voucher.get("iv"),
        format="aaxc",
        raw={"codec": codec},
    )
