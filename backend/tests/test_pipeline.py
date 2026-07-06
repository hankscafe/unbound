"""Pipeline tests: download headers and path templating."""

from __future__ import annotations

from app.pipeline import download as d
from app.pipeline.templates import build_output_path, sanitize
from app.db.models import AudioFormat, Book, LibraryProfile


def test_download_sends_audible_user_agent(monkeypatch, tmp_path):
    """Regression: the Audible CDN 403s without the Audible app User-Agent."""
    captured: dict = {}

    class FakeResp:
        headers = {"content-length": "4"}

        def raise_for_status(self):
            pass

        def iter_bytes(self, chunk_size=0):
            yield b"data"

    class FakeStream:
        def __init__(self, method, url, headers=None, **kw):
            captured["headers"] = headers

        def __enter__(self):
            return FakeResp()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(d.httpx, "stream", lambda *a, **k: FakeStream(*a, **k))
    dest = tmp_path / "book.aaxc"
    d.download_file("https://cdn.example/file", dest)

    assert captured["headers"]["User-Agent"] == d.AUDIBLE_USER_AGENT
    assert dest.exists() and dest.read_bytes() == b"data"


def test_download_allows_header_override(monkeypatch, tmp_path):
    captured: dict = {}

    class FakeStream:
        def __init__(self, method, url, headers=None, **kw):
            captured["headers"] = headers

        def __enter__(self):
            class R:
                headers = {"content-length": "0"}

                def raise_for_status(self):
                    pass

                def iter_bytes(self, chunk_size=0):
                    return iter(())

            return R()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(d.httpx, "stream", lambda *a, **k: FakeStream(*a, **k))
    d.download_file("https://x/y", tmp_path / "f.aaxc", headers={"X-Test": "1"})
    assert captured["headers"]["X-Test"] == "1"
    assert captured["headers"]["User-Agent"] == d.AUDIBLE_USER_AGENT


def test_path_templating_collapses_empty_and_sanitizes():
    profile = LibraryProfile(
        name="p", root_path="/lib", folder_template="{author}/{series}",
        filename_template="{title}", audio_format=AudioFormat.m4b,
    )
    # Book with a series.
    b1 = Book(audible_account_id=1, asin="A1", title="The Final Empire",
              authors="Brandon Sanderson", series="Mistborn", series_sequence="1")
    p1 = build_output_path(b1, profile)
    assert p1.as_posix() == "/lib/Brandon Sanderson/Mistborn/The Final Empire.m4b"

    # Book with NO series -> empty {series} segment collapses (no empty folder).
    b2 = Book(audible_account_id=1, asin="A2", title="Standalone",
              authors="Some Author", series=None, series_sequence=None)
    p2 = build_output_path(b2, profile)
    assert p2.as_posix() == "/lib/Some Author/Standalone.m4b"


def test_sanitize_strips_illegal_chars():
    assert sanitize('a/b:c*?"<>|d') == "a_b_c______d"
    assert sanitize("") == "Unknown"


def test_template_artifact_from_empty_token_is_trimmed():
    # "{series_seq} - {title}" with no sequence must not yield "- Title".
    profile = LibraryProfile(
        name="p", root_path="/lib", folder_template="{author}",
        filename_template="{series_seq} - {title}", audio_format=AudioFormat.m4b,
    )
    b = Book(audible_account_id=1, asin="A", title="Standalone", authors="Author",
             series=None, series_sequence=None)
    assert build_output_path(b, profile).as_posix() == "/lib/Author/Standalone.m4b"
    # With a sequence it stays intact.
    b2 = Book(audible_account_id=1, asin="B", title="Book Two", authors="Author",
              series="S", series_sequence="2")
    assert build_output_path(b2, profile).as_posix() == "/lib/Author/2 - Book Two.m4b"


def test_build_ffmeta_chapters():
    from app.pipeline.chapters import build_ffmeta

    doc = build_ffmeta(
        [
            {"title": "Opening Credits", "start_ms": 0, "length_ms": 87578},
            {"title": "Alien III", "start_ms": 87578, "length_ms": 8007373},
        ]
    )
    assert doc.startswith(";FFMETADATA1")
    assert "[CHAPTER]" in doc
    assert "TIMEBASE=1/1000" in doc
    assert "START=0\nEND=87578\ntitle=Opening Credits" in doc
    # Second chapter END = start + length.
    assert "START=87578\nEND=8094951" in doc


def test_ffmeta_escapes_special_chars():
    from app.pipeline.chapters import build_ffmeta

    doc = build_ffmeta([{"title": "Part 1 = the; start #x", "start_ms": 0, "length_ms": 10}])
    assert "title=Part 1 \\= the\\; start \\#x" in doc


def test_download_resume_appends(monkeypatch, tmp_path):
    """A prior .part is resumed via Range and the final file is complete."""
    full = b"0123456789ABCDEF"
    dest = tmp_path / "f.aaxc"
    (tmp_path / "f.aaxc.part").write_bytes(full[:6])  # 6 bytes already present

    class FakeStream:
        def __init__(self, method, url, headers=None, **kw):
            self.h = headers or {}

        def __enter__(self):
            rng = self.h.get("Range")
            start = int(rng.split("=")[1].split("-")[0]) if rng else 0
            body = full[start:]
            resp = type("R", (), {})()
            resp.status_code = 206 if rng else 200
            resp.headers = {"content-length": str(len(body))}
            if rng:
                resp.headers["content-range"] = f"bytes {start}-{len(full) - 1}/{len(full)}"
            resp.raise_for_status = lambda: None
            resp.iter_bytes = lambda chunk_size=0: iter([body])
            resp.close = lambda: None
            return resp

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(d.httpx, "stream", lambda *a, **k: FakeStream(*a, **k))
    d.download_file("https://x/y", dest, resume=True)
    assert dest.read_bytes() == full  # 6 existing + 10 resumed = complete


def test_storage_detects_unc_path():
    from app.services.storage import _looks_like_unc_or_windows

    assert _looks_like_unc_or_windows(r"\\fileserver\Media\audiobooks") is True
    assert _looks_like_unc_or_windows(r"C:\Users\me\books") is True
    assert _looks_like_unc_or_windows("/data/library") is False
    assert _looks_like_unc_or_windows("data/library") is False


def test_path_problem_accepts_absolute_rejects_unc(monkeypatch):
    from app.services import storage

    # Simulate the Linux container (a UNC path is valid/absolute on a Windows host).
    monkeypatch.setattr(storage.os, "name", "posix")
    assert storage.path_problem("/data/library/audiobooks") is None
    assert storage.path_problem(r"\\fileserver\Media\audiobooks") is not None
    assert storage.path_problem("relative/path") is not None
    assert storage.path_problem("") is not None


def test_transient_classifier():
    import httpx
    from app.worker.tasks import _transient

    req = httpx.Request("GET", "https://x/y")
    resp403 = httpx.Response(403, request=req)
    resp404 = httpx.Response(404, request=req)
    assert _transient(httpx.HTTPStatusError("x", request=req, response=resp403)) is True
    assert _transient(httpx.HTTPStatusError("x", request=req, response=resp404)) is False
    assert _transient(httpx.ConnectError("boom")) is True
    assert _transient(ValueError("nope")) is False
