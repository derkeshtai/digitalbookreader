"""PDF -> streaming page source (rendered on demand with PyMuPDF).

Pages are rendered lazily at :data:`DPI` and served as PNG, so opening a large PDF is fast
and only visible pages consume CPU/memory. The same source backs both the reader (over the
loopback HTTP server) and the export-to-images feature.
"""
from pathlib import Path

from ..pagesource import PageSource, _LRU

DPI = 150  # render resolution for on-demand page images


class PdfPageSource(PageSource):
    def __init__(self, path: str | Path, dpi: int = DPI) -> None:
        super().__init__()
        self.path = Path(path)
        self.dpi = dpi
        self._doc = None
        self._count = 0
        self._cache = _LRU(24)

    def _ensure(self) -> None:
        if self._doc is None:
            import pymupdf
            self._doc = pymupdf.open(str(self.path))
            self._count = int(self._doc.page_count or 0)

    @property
    def total_pages(self) -> int:
        with self._lock:
            self._ensure()
            return max(0, self._count)

    def get_toc(self) -> list[tuple[str, str]]:
        """Outline entries as ``(title, "p<page>")`` anchors (1-based page numbers)."""
        out: list[tuple[str, str]] = []
        with self._lock:
            self._ensure()
            try:
                for _level, title, page in self._doc.get_toc():
                    if title and 1 <= int(page) <= self._count:
                        out.append((title.strip(), f"p{int(page)}"))
            except Exception:  # noqa: BLE001 - no outline / unreadable -> empty TOC
                pass
        return out

    def page_image(self, index: int):
        if index < 1:
            return None
        with self._lock:
            self._ensure()
            if index > self._count:
                return None
            cached = self._cache.get(index)
            if cached is not None:
                return cached
            import pymupdf
            zoom = self.dpi / 72.0
            pix = self._doc[index - 1].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
            res = (pix.tobytes("png"), "image/png")
            self._cache.put(index, res)
            return res

    def close(self) -> None:
        with self._lock:
            if self._doc is not None:
                try:
                    self._doc.close()
                except Exception:  # noqa: BLE001
                    pass
                self._doc = None


def parse_pdf(path: str | Path):
    from . import BookContent

    p = Path(path)
    src = PdfPageSource(p)
    n = max(1, src.total_pages)

    parts = [
        "<style>",
        ".comic img { display:block; margin:10px auto; height:auto;",
        "  max-width: calc(96% * var(--zoom-scale, 1)); }",
        ".comic section { column-count:1 !important; break-inside:avoid; }",
        "</style>",
        '<div class="comic">',
    ]
    for i in range(1, n + 1):
        parts.append(f'<section id="p{i}"><img src="page/{i}" alt="Página {i}" loading="lazy"></section>')
    parts.append("</div>")

    return BookContent(
        html="\n".join(parts),
        toc=src.get_toc(),
        title=p.stem,
        author="",
        total_pages=n,
        page_source=src,
    )
