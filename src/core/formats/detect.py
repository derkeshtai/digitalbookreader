"""Content-based book/document format detection (magic bytes and ZIP metadata).

Detection is deliberately tolerant of wrong or missing filename extensions. It
recognizes the formats supported by :mod:`src.core.formats` and returns a
canonical kind string used for parser dispatch.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

_RAR = b"Rar!\x1a\x07"
_ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_7Z = b"\x37\x7a\xbc\xaf\x27\x1c"


def _head(path: Path, n: int = 8192) -> bytes:
    with path.open("rb") as fh:
        return fh.read(n)


def _zip_kind(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            names = {name.lower() for name in archive.namelist()}
            if "mimetype" in names:
                name = next(name for name in archive.namelist() if name.lower() == "mimetype")
                mimetype = archive.read(name).strip().lower()
                if mimetype == b"application/epub+zip":
                    return "epub"
                if mimetype == b"application/vnd.oasis.opendocument.text":
                    return "odt"
            if "meta-inf/container.xml" in names:
                return "epub"
            if "[content_types].xml" in names and "word/document.xml" in names:
                return "docx"
            if "content.xml" in names and "meta-inf/manifest.xml" in names:
                # ODT files normally have the mimetype entry too; this is a fallback
                # for files produced by less strict office suites.
                return "odt"
    except (OSError, zipfile.BadZipFile, RuntimeError):
        pass
    return "zip"


def detect_kind(path: str | Path) -> str:
    """Return a canonical kind or ``unknown`` for *path*."""
    p = Path(path)
    try:
        head = _head(p)
    except OSError:
        return "unknown"
    if not head:
        return "unknown"

    if head.startswith(_RAR):
        return "rar"
    if head.startswith(_7Z):
        return "7z"
    if b"%PDF-" in head[:1024]:
        return "pdf"
    if head.lstrip().startswith(b"{\\rtf"):
        return "rtf"

    # Palm Database files commonly identify themselves as BOOKMOBI at byte 60.
    # Some PRC variants use TEXtREAd; the extension fallback handles those too.
    if len(head) >= 68 and head[60:68] in (b"BOOKMOBI", b"TEXtREAd"):
        return "mobi"
    if b"MOBI" in head[:8192]:
        return "mobi"

    if head[:4] in _ZIP_SIGNATURES:
        return _zip_kind(p)

    low = head[:4096].lower()
    if b"fictionbook" in low or b"<fbk" in low:
        return "fb2"
    if b"<!doctype html" in low or b"<html" in low or b"<body" in low:
        return "html"

    if _looks_like_text(head[:2048]):
        return "txt"
    return "unknown"


def _looks_like_text(data: bytes) -> bool:
    """Heuristic for plain text which excludes binary data and NUL-heavy files."""
    if not data or data.count(0) > len(data) * 0.01:
        return False
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = data.decode("cp1252")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
    printable = sum(char.isprintable() or char.isspace() for char in text)
    controls = sum(byte < 9 or 13 < byte < 32 for byte in data)
    return bool(text) and printable / len(text) > 0.95 and controls < len(data) * 0.02


def kind_to_ext(kind: str) -> str | None:
    """Map a detected kind to a representative supported extension."""
    return {
        "epub": ".epub", "zip": ".cbz", "rar": ".cbr", "7z": ".7z",
        "pdf": ".pdf", "fb2": ".fb2", "txt": ".txt", "docx": ".docx",
        "odt": ".odt", "rtf": ".rtf", "html": ".html", "markdown": ".md",
        "mobi": ".mobi",
    }.get(kind)
