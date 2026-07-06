"""Audible marketplace helpers (locale → website domain, product URLs)."""

from __future__ import annotations

# Marketplace/locale code -> Audible website domain.
DOMAINS = {
    "us": "www.audible.com",
    "uk": "www.audible.co.uk",
    "de": "www.audible.de",
    "fr": "www.audible.fr",
    "ca": "www.audible.ca",
    "au": "www.audible.com.au",
    "jp": "www.audible.co.jp",
    "it": "www.audible.it",
    "es": "www.audible.es",
    "in": "www.audible.in",
    "br": "www.audible.com.br",
}


def audible_product_url(asin: str, marketplace: str) -> str | None:
    """Public Audible product page for a title (redirects to the canonical slug)."""
    if not asin:
        return None
    domain = DOMAINS.get((marketplace or "us").lower(), DOMAINS["us"])
    return f"https://{domain}/pd/{asin}"
