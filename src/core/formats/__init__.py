"""Format registry: parse any supported book into renderable HTML / a page source.

Implemented now: EPUB, TXT, FB2, PDF and comic archives (CBZ/CBR/ZIP/7Z). Comics and PDFs
are *streaming* — they expose an on-demand :class:`~src.core.pagesource.PageSource` instead
of materialising every page up front, which keeps opening fast.

Dispatch is by **content** (magic bytes via :func:`detect.detect_kind`) with a fallback to
the file extension, so a mislabelled ``.cbr`` that is really a ZIP/7z still opens correctly.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class BookContent:
    html: str                        # body HTML (no <html> wrapper)
    toc: list[tuple[str, str]]       # [(title, anchor_id), ...]
    title: str
    author: str
    cover_path: Optional[Path] = None
    total_pages: int = 1
    base_url: Optional[str] = None   # legacy local dir for relative resources
    page_source: Optional[object] = None  # on-demand PageSource (streaming books)


class NotSupportedError(Exception):
    def __init__(self, fmt: str) -> None:
        super().__init__(f"Formato '{fmt}' aún no implementado.")

SUPPORTED_EXTS = {".epub", ".mobi", ".prc", ".fb2", ".pdf", ".txt",
                  ".cbz", ".cbr", ".zip", ".7z"}

# extension -> content kind, used when magic-byte detection is inconclusive.
_EXT_KIND = {
    ".epub": "epub", ".pdf": "pdf", ".fb2": "fb2", ".txt": "txt",
    ".cbz": "zip", ".zip": "zip", ".cbr": "rar", ".7z": "7z",
}


def parse(path: str | Path) -> BookContent:
    """Parse *path* into :class:`BookContent`, dispatching by detected content kind."""
    from .detect import detect_kind

    p = Path(path)
    ext = p.suffix.lower()
    if ext not in SUPPORTED_EXTS:
        raise NotSupportedError(ext or "desconocido")

    # Trust the file's actual contents first; fall back to what the extension implies.
    kind = detect_kind(p)
    if kind == "unknown":
        kind = _EXT_KIND.get(ext, "unknown")

    from .epub import parse_epub
    from .txt import parse_txt
    from .fb2 import parse_fb2
    from .comic import parse_comic
    from .pdf import parse_pdf
    if kind == "epub":
        return parse_epub(p)
    if kind == "txt":
        return parse_txt(p)
    if kind == "fb2":
        return parse_fb2(p)
    if kind in ("zip", "rar", "7z"):
        return parse_comic(p, kind=kind)
    if kind == "pdf":
        return parse_pdf(p)

    # Detected as a known-but-unimplemented format (mobi/prc), or still unknown.
    raise NotSupportedError(ext.lstrip(".") or kind)
