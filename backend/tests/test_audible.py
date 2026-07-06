"""Mocked tests for the Audible integration logic.

These do NOT hit the network or require a specific ``audible`` version: a fake
``audible`` module is injected so we can exercise our own code — auth-blob
(de)serialization, the voucher-decrypting license request, and the threaded
guided-link OTP state machine — deterministically.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from app.audible import client as ac
from app.audible.linking import LinkFlowManager


class FakeAuth:
    def __init__(self, data: dict | None = None):
        self.data = data or {"access_token": "Atna|token"}
        self.customer_info = {"email": "me@example.com", "user_id": "amzn1.account.X"}
        self.device_info = {"device_serial_number": "S123", "device_type": "T456"}

    def to_file(self, path, encryption=False):
        Path(path).write_text(json.dumps(self.data), encoding="utf-8")

    @classmethod
    def from_file(cls, path):
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def get_activation_bytes(self, extract=True):
        return "deadbeef"


LICENSE_OK = {
    "content_license": {
        "status_code": "Granted",
        "asin": "B00TEST",
        "license_response": "ENCRYPTED_VOUCHER_BLOB",
        "content_metadata": {
            "content_url": {"offline_url": "https://cds.audible.com/download/B00TEST.aaxc"},
            "content_reference": {"content_format": "AAX_44_128"},
        },
    }
}
LICENSE_DENIED = {"content_license": {"status_code": "Denied", "message": "Not entitled"}}


class FakeClient:
    _response = LICENSE_OK

    def __init__(self, auth=None, **kw):
        self.auth = auth

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, path, body=None, headers=None):
        assert "licenserequest" in path
        assert headers and "X-Amzn-RequestId" in headers  # required headers present
        return self._response

    def get(self, path, **kw):
        return {"items": []}


def _fake_audible_module() -> types.ModuleType:
    mod = types.ModuleType("audible")
    mod.Authenticator = FakeAuth
    mod.Client = FakeClient
    return mod


@pytest.fixture
def fake_audible(monkeypatch):
    """Patch the client's ``audible`` binding + inject a fake aescipher voucher decryptor."""
    fake = _fake_audible_module()
    monkeypatch.setattr(ac, "audible", fake)
    monkeypatch.setattr(ac, "AUDIBLE_AVAILABLE", True)

    aescipher = types.ModuleType("audible.aescipher")
    aescipher.decrypt_voucher_from_licenserequest = lambda auth, lr: {
        "key": "0011223344556677",
        "iv": "8899aabbccddeeff",
    }
    monkeypatch.setitem(sys.modules, "audible.aescipher", aescipher)
    return fake


def test_auth_blob_roundtrip(fake_audible):
    auth = FakeAuth({"access_token": "Atna|abc", "adp_token": "{enc:x}"})
    blob = ac.serialize_auth(auth)
    assert "Atna|abc" in blob  # serialized JSON
    restored = ac.deserialize_auth(blob)
    assert restored.data["access_token"] == "Atna|abc"


def test_get_aaxc_license_decrypts_voucher(fake_audible):
    FakeClient._response = LICENSE_OK
    lic = ac.get_aaxc_license(FakeAuth(), "B00TEST")
    assert lic.format == "aaxc"
    assert lic.download_url.endswith("B00TEST.aaxc")
    assert lic.key == "0011223344556677"
    assert lic.iv == "8899aabbccddeeff"


def test_get_aaxc_license_denied_raises(fake_audible):
    FakeClient._response = LICENSE_DENIED
    with pytest.raises(ac.LicenseError):
        ac.get_aaxc_license(FakeAuth(), "B00TEST")
    FakeClient._response = LICENSE_OK  # restore for other tests


def test_response_groups_exclude_available_codecs():
    """Regression: 'available_codecs' is not a valid *requested* library group (400)."""
    assert "available_codecs" not in ac._RESPONSE_GROUPS


def test_normalize_item_detects_aax_and_fields():
    """Regression against real library JSON shape (verified on a live account)."""
    item = {
        "asin": "B00TEST",
        "title": "The Thousand Orcs",
        "subtitle": None,
        "authors": [{"asin": "A1", "name": "R. A. Salvatore"}],
        "narrators": [{"name": "Victor Bevine"}],
        "series": [{"asin": "S1", "title": "Legend of Drizzt", "sequence": "17"}],
        "runtime_length_min": 810,
        "product_images": {"500": "https://img/500.jpg"},
        "available_codecs": [{"name": "aax_22_64"}, {"name": "mp4_22_64"}, {"name": "aax"}],
    }
    norm = ac._normalize_item(item)
    assert norm.title == "The Thousand Orcs"
    assert norm.authors == "R. A. Salvatore"
    assert norm.narrators == "Victor Bevine"
    assert norm.series == "Legend of Drizzt"
    assert norm.series_sequence == "17"
    assert norm.runtime_minutes == 810
    assert norm.cover_url == "https://img/500.jpg"
    assert norm.is_aax_available is True


def test_safe_error_redacts_signed_urls():
    from app.worker.tasks import _safe_error

    exc = Exception(
        "Client error '403 Forbidden' for url "
        "'https://cds.audible.com/x.aax?Signature=SECRET&Key-Pair-Id=ABC&Expires=1'"
    )
    out = _safe_error(exc)
    assert "Signature=SECRET" not in out
    assert "Key-Pair-Id" not in out
    assert "https://cds.audible.com/x.aax?<redacted>" in out
    assert "403 Forbidden" in out  # non-sensitive context preserved


def test_is_auth_error_distinguishes_failures():
    from app.worker.tasks import _is_auth_error

    class Err(Exception):
        def __init__(self, code):
            self.code = code

    assert _is_auth_error(Err(401)) is True
    assert _is_auth_error(Err(403)) is True
    assert _is_auth_error(Err(400)) is False
    assert _is_auth_error(ValueError("boom")) is False


def test_guided_link_otp_flow(fake_audible):
    """The threaded guided flow should surface an OTP prompt then complete."""

    def fake_from_login(email, password, locale, with_username=False,
                        otp_callback=None, captcha_callback=None,
                        cvf_callback=None, approval_callback=None):
        code = otp_callback()  # blocks until the API supplies the OTP
        assert code == "123456"
        return FakeAuth({"access_token": "Atna|linked"})

    fake_audible.Authenticator.from_login = staticmethod(fake_from_login)

    mgr = LinkFlowManager()
    flow_id, step = mgr.start_guided(email="me@x.com", password="pw", marketplace="us")
    assert step.status == "needs_otp"
    assert step.data["flow_id"] == flow_id

    step2 = mgr.provide(flow_id, "otp", "123456")
    assert step2.status == "linked"
    assert step2.data["account_owner_email"] == "me@example.com"

    blob = mgr.take_result(flow_id)
    assert blob and "Atna|linked" in blob
