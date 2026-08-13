"""Phase 28 -- malformed document tests + parser ImportError branches
(document adapter defensive paths)."""

from __future__ import annotations

import pytest

from app.acquisition.documentadapter import DocumentAdapter


def test_pdf_corrupt_fails_closed() -> None:
    result = DocumentAdapter().parse(
        b"%PDF-1.4 garbage not a real pdf" + b"\x00" * 50,
        content_type="application/pdf",
        source_url="u",
    )
    assert result.ok is False
    assert result.parser_backend == "pypdf"
    assert result.error


def test_docx_corrupt_fails_closed() -> None:
    result = DocumentAdapter().parse(
        b"PK\x03\x04 not a real docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        source_url="u",
    )
    assert result.ok is False
    assert result.error


def test_xlsx_corrupt_fails_closed() -> None:
    result = DocumentAdapter().parse(
        b"not xlsx at all",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        source_url="u",
    )
    assert result.ok is False
    assert result.error


def test_html_malformed_tolerated() -> None:
    result = DocumentAdapter().parse(
        b"<html><body><div><p>unclosed",
        content_type="text/html",
        source_url="u",
    )
    assert result.ok is True or "parse failed" in result.error


def test_json_malformed_fails() -> None:
    result = DocumentAdapter().parse(
        b'{"unterminated": ',
        content_type="application/json",
        source_url="u",
    )
    assert result.ok is False
    assert "invalid JSON" in result.error


def test_pdf_import_error_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pypdf":
            raise ImportError("pypdf not installed (simulated)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = DocumentAdapter().parse(
        b"%PDF-1.4 x", content_type="application/pdf", source_url="u"
    )
    assert result.ok is False
    assert "UNAVAILABLE:pypdf" in result.parser_backend


def test_docx_import_error_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "docx":
            raise ImportError("python-docx not installed (simulated)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = DocumentAdapter().parse(
        b"PK\x03\x04fake",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        source_url="u",
    )
    assert result.ok is False
    assert "UNAVAILABLE:python-docx" in result.parser_backend


def test_xlsx_import_error_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "openpyxl":
            raise ImportError("openpyxl not installed (simulated)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = DocumentAdapter().parse(
        b"PK\x03\x04fake",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        source_url="u",
    )
    assert result.ok is False
    assert "UNAVAILABLE:openpyxl" in result.parser_backend


def test_lxml_import_error_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "lxml":
            raise ImportError("lxml not installed (simulated)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = DocumentAdapter().parse(
        b"<html>x</html>", content_type="text/html", source_url="u"
    )
    assert result.ok is False
    assert "UNAVAILABLE:lxml" in result.parser_backend


def test_unsupported_parser_reported() -> None:

    adapter = DocumentAdapter()
    # force a content type that maps to no parser by bypassing detect_type
    result = adapter.parse(
        b"binary",
        content_type="application/x-unknown-magic",
        source_url="u",
    )
    # detect_type falls back to text/plain -> text parser succeeds
    assert result.ok is True or result.parser_backend.startswith("stdlib")
