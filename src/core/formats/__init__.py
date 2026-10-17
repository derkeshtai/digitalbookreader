"""Format registry for books and office documents.

Supported input includes EPUB, unencrypted MOBI/PRC/AZW/AZW3, FB2, PDF, TXT,
HTML/XHTML, Markdown, RTF, DOCX, ODT, and image-based comic archives (CBZ/CBR/ZIP/7Z).
PDFs and comic archives are streamed page-by-page; document parsers return HTML.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class BookContent:
    html: str
    toc: list[tuple[str, str]]
    title: str
    author: str
    cover_path: Optional[Path] = None
    total_pages: int = 1
    base_url: Optional[str] = None
    page_source: Optional[object] = None


class NotSupportedError(Exception):
    def __init__(self, fmt: str) -> None:
        super().__init__(f"Formato '{fmt}' aún no es compatible o no se reconoce.")


SUPPORTED_EXTS = {
    ".epub", ".mobi", ".prc", ".azw", ".azw3", ".fb2", ".pdf", ".txt",
    ".cbz", ".cbr", ".zip", ".7z", ".html", ".htm", ".xhtml", ".md",
    ".markdown", ".rtf", ".docx", ".odt",
}

# Extension fallback for damaged, encrypted, or otherwise inconclusive headers.
_EXT_KIND = {
    ".epub": "epub", ".pdf": "pdf", ".fb2": "fb2", ".txt": "txt",
    ".cbz": "zip", ".zip": "zip", ".cbr": "rar", ".7z": "7z",
    ".mobi": "mobi", ".prc": "mobi", ".azw": "mobi", ".azw3": "mobi",
    ".html": "html", ".htm": "html", ".xhtml": "html",
    ".md": "markdown", ".markdown": "markdown", ".rtf": "rtf",
    ".docx": "docx", ".odt": "odt",
}


def parse(path: str | Path) -> BookContent:
    """Parse *path* based on its detected contents, then fall back to its extension."""
    from .detect import detect_kind

    p = Path(path)
    kind = detect_kind(p)
    ext_kind = _EXT_KIND.get(p.suffix.lower())
    # Markdown is text by nature, so magic-byte detection correctly calls it text;
    # its explicit extension is the only reliable way to select Markdown rendering.
    if kind == "txt" and ext_kind in {"markdown", "mobi"}:
        kind = ext_kind
    if kind == "unknown":
        kind = ext_kind or "unknown"
    if kind == "unknown":
        raise NotSupportedError(p.suffix.lstrip(".") or "desconocido")

    if kind == "epub":
        from .epub import parse_epub
        return parse_epub(p)
    if kind == "txt":
        from .txt import parse_txt
        return parse_txt(p)
    if kind == "fb2":
        from .fb2 import parse_fb2
        return parse_fb2(p)
    if kind in {"zip", "rar", "7z"}:
        from .comic import parse_comic
        return parse_comic(p, kind=kind)
    if kind == "pdf":
        from .pdf import parse_pdf
        return parse_pdf(p)
    if kind == "mobi":
        from .mobi import parse_mobi
        return parse_mobi(p)
    if kind == "html":
        from .html import parse_html
        return parse_html(p)
    if kind == "markdown":
        from .markdown import parse_markdown
        return parse_markdown(p)
    if kind == "rtf":
        from .rtf import parse_rtf
        return parse_rtf(p)
    if kind == "docx":
        from .docx import parse_docx
        return parse_docx(p)
    if kind == "odt":
        from .odt import parse_odt
        return parse_odt(p)

    raise NotSupportedError(p.suffix.lstrip(".") or kind)
