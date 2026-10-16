"""Content-based format detection (magic bytes).

Used to route a file to the right parser based on what it *actually* is, not just
its extension. This fixes files whose name lies about their contents — e.g. a
``.cbr`` that is really a ZIP or 7z archive no longer fails with "not an unrar
file", and extensionless books open correctly too.

The returned ``kind`` is one of:
    epub | zip | rar | 7z | pdf | fb2 | mobi | txt | unknown
"""
import zipfile
from pathlib import Path

_RAR = b"Rar!\x1a\x07"                 # RAR4 / RAR5 header (first 6 bytes)
_ZIP_LOCAL = b"PK\x03\x04"             # local file header
_ZIP_EMPTY = b"PK\x05\x06"             # empty archive
_ZIP_SPAN = b"PK\x07\x08"              # spanned archive
_7Z = b"\x37\x7a\xbc\xaf\x27\x1c"      # 7z signature


def _head(path: Path, n: int = 8192) -> bytes:
    with open(path, "rb") as fh:
        return fh.read(n)


def detect_kind(path: str | Path) -> str:
    """Return the canonical content kind for *path* (see module docstring)."""
    p = Path(path)
    try:
        head = _head(p)
    except OSError:
        return "unknown"
    if not head:
        return "unknown"

    # RAR archives (a real CBR). Checked before ZIP — the signatures differ.
    if head.startswith(_RAR):
        return "rar"
    # 7z archives.
    if head[:6] == _7Z:
        return "7z"
    # PDF (allow a small amount of leading whitespace / BOM).
    if b"%PDF-" in head[:1024]:
        return "pdf"

    # ZIP family: CBZ, EPUB or plain image zip. Distinguish EPUB by marker files.
    if any(head[:4] == z for z in (_ZIP_LOCAL, _ZIP_EMPTY, _ZIP_SPAN)):
        try:
            with zipfile.ZipFile(p) as zf:
                lower = {n.lower() for n in zf.namelist()}
            if "mimetype" in lower or "meta-inf/container.xml" in lower:
                return "epub"
        except Exception:  # noqa: BLE001 - not a readable zip; treat as plain zip
            pass
        return "zip"

    low = head[:4096].lower()
    # FB2 (FictionBook) is XML with an <fbk...> root or a FictionBook tag.
    if b"<fbk" in low or b"fictionbook" in low:
        return "fb2"

    # MOBI / AZW3 (PalmDOC): first byte 0x78/0x99 and 'MOBI' at offset 34.
    if len(head) >= 38 and head[0] in (0x78, 0x99) and head[34:38] == b"MOBI":
        return "mobi"

    # Plain-text fallback heuristic.
    if _looks_like_text(head[:2048]):
        return "txt"

    return "unknown"


def _looks_like_text(b: bytes) -> bool:
    """Heuristic: mostly printable, decodable as UTF-8/Latin-1, few control chars."""
    if not b:
        return False
    if b.count(0) > len(b) * 0.01:          # NULs => binary
        return False
    try:
        s = b.decode("utf-8")
    except UnicodeDecodeError:
        try:
            s = b.decode("latin-1")
        except Exception:  # noqa: BLE001
            return False
    printable = sum(ch.isprintable() or ch.isspace() for ch in s)
    ctrl = sum(1 for c in b if (c < 9 or (13 < c < 32)))
    return (printable / len(s)) > 0.95 and ctrl < len(b) * 0.02


def kind_to_ext(kind: str) -> str | None:
    """Map a detected kind to the extension the parser dispatch expects."""
    return {
        "epub": ".epub", "zip": ".cbz", "rar": ".cbr", "7z": ".7z",
        "pdf": ".pdf", "fb2": ".fb2", "txt": ".txt",
    }.get(kind)
