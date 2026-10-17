"""Comic archive reader: CBZ, CBR and image ZIP/7Z archives (streaming).

Instead of extracting every page to disk up front (which made large books slow to open),
pages are served on demand through a :class:`ComicPageSource`. The reader renders the book
with relative ``<img src="page/N">`` tags over the loopback HTTP server, so only visible
pages are ever read from the archive.

The archive kind (zip / rar / 7z) is normally supplied by the content-based detector in
:mod:`detect`; when unknown it is inferred from the extension and then re-checked against
the file's magic bytes so a mislabelled ``.cbr`` no longer fails with "not an unrar file".
"""
import re
import zipfile
from pathlib import Path

from ..pagesource import _LRU, PageSource

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
MAX_PAGE_BYTES = 64 * 1024 * 1024

_MIME_BY_EXT = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
}


def mime_for_ext(ext: str) -> str:
    return _MIME_BY_EXT.get((ext or "").lower(), "application/octet-stream")


def _natural_sort_key(name: str):
    return tuple(int(part) if part.isdigit() else part.casefold()
                 for part in re.split(r"(\d+)", name))


def kind_from_path(path: Path) -> str | None:
    """Best-effort archive kind for *path* (zip/rar/7z), else ``None``."""
    from .detect import detect_kind

    k = detect_kind(path)
    if k in ("rar", "zip", "7z"):
        return k
    # Content detection was inconclusive; fall back to the extension.
    e = path.suffix.lower()
    if e == ".cbr":
        return "rar"
    if e in (".cbz", ".zip"):
        return "zip"
    if e == ".7z":
        return "7z"
    return None


class ComicPageSource(PageSource):
    """Lazily reads image members from a comic archive, one page at a time."""

    def __init__(self, path: str | Path, kind: str | None = None) -> None:
        super().__init__()
        self.path = Path(path)
        self.kind = kind or kind_from_path(self.path)
        if self.kind not in ("rar", "zip", "7z"):
            raise ValueError(f"unsupported comic archive kind: {self.kind!r}")
        self._handle = None
        self._names: list[str] | None = None
        self._cache = _LRU(40)

    # -- handle management -----------------------------------------------------
    def _open(self) -> None:
        if self._handle is not None:
            return
        kind = self.kind
        if kind == "rar":
            import rarfile

            from .rar_tool import configure_unrar
            configure_unrar()
            h = rarfile.RarFile(str(self.path))
        elif kind == "7z":
            import py7zr
            h = py7zr.SevenZipFile(str(self.path))
        else:  # zip / cbz
            h = zipfile.ZipFile(str(self.path))
        self._handle = h
        if self._names is None:
            try:
                self._names = self._list_names(h, kind)
            except Exception:
                self._handle = None
                try:
                    h.close()
                except OSError:
                    pass
                raise

    @staticmethod
    def _member_names(h, kind: str) -> list[str]:
        names = h.getnames() if kind == "7z" else h.namelist()
        pages = [n for n in names
                 if not n.endswith("/") and Path(n).suffix.lower() in IMAGE_EXTS]
        return sorted(pages, key=_natural_sort_key)

    def _list_names(self, h, kind: str) -> list[str]:
        try:
            return self._member_names(h, kind)
        except Exception as e:
            raise ValueError(
                f"No se pudo leer el archivo como {kind.upper()} "
                f"(¿está dañado o con otra extensión real?): {e}"
            ) from e

    def _read_member(self, name: str) -> bytes:
        kind = self.kind
        h = self._handle
        if kind == "7z":
            # py7zr 1.x removed SevenZipFile.read(); use an in-memory writer factory
            # instead of extracting the whole archive or writing untrusted paths to disk.
            if hasattr(h, "read"):
                result = h.read([name])
                for directory in result.values():
                    for bio in directory.values():
                        data = bio.read()
                        if len(data) > MAX_PAGE_BYTES:
                            raise ValueError(f"La imagen {name!r} excede el límite de tamaño.")
                        return data
                raise KeyError(name)
            entry = next((item for item in h.list() if item.filename == name), None)
            if entry and entry.uncompressed and entry.uncompressed > MAX_PAGE_BYTES:
                raise ValueError(f"La imagen {name!r} excede el límite de tamaño.")
            from py7zr.io import BytesIOFactory
            factory = BytesIOFactory(limit=MAX_PAGE_BYTES)
            try:
                h.extract(targets=[name], factory=factory)
                data = factory.get(name).read()
            finally:
                # A SevenZipFile worker retains decoder state after extraction.
                # Reset it so later random page requests do not fail CRC checks.
                if hasattr(h, "reset"):
                    h.reset()
            if len(data) > MAX_PAGE_BYTES:
                raise ValueError(f"La imagen {name!r} excede el límite de tamaño.")
            return data
        info = h.getinfo(name)
        if info.file_size > MAX_PAGE_BYTES:
            raise ValueError(f"La imagen {name!r} excede el límite de {MAX_PAGE_BYTES // (1024 * 1024)} MB.")
        return h.read(name)

    # -- PageSource API --------------------------------------------------------
    @property
    def total_pages(self) -> int:
        with self._lock:
            self._open()
            return len(self._names or [])

    def page_image(self, index: int):
        if index < 1:
            return None
        with self._lock:
            self._open()
            names = self._names or []
            if index > len(names):
                return None
            cached = self._cache.get(index)
            if cached is not None:
                return cached
            name = names[index - 1]
            data = self._read_member(name)
            res = (data, mime_for_ext(Path(name).suffix))
            self._cache.put(index, res)
            return res

    def close(self) -> None:
        with self._lock:
            if self._handle is not None:
                try:
                    self._handle.close()
                except Exception:  # noqa: BLE001
                    pass
                self._handle = None


def _comic_html_streaming(total: int) -> tuple[str, list[tuple[str, str]]]:
    parts = [
        "<style>",
        ".comic img { display:block; margin:10px auto; height:auto;",
        "  max-width: calc(96% * var(--zoom-scale, 1)); }",
        ".comic section { column-count:1 !important; break-inside:avoid; }",
        "</style>",
        '<div class="comic">',
    ]
    toc: list[tuple[str, str]] = []
    for i in range(1, total + 1):
        parts.append(f'<section id="p{i}"><img src="page/{i}" alt="Página {i}" loading="lazy"></section>')
        toc.append((f"Página {i}", f"p{i}"))
    parts.append("</div>")
    return "\n".join(parts), toc


def parse_comic(path: str | Path, kind: str | None = None):
    from . import BookContent, NotSupportedError

    p = Path(path)
    src = ComicPageSource(p, kind=kind)  # raises ValueError for a bad/unsupported archive
    n = src.total_pages
    if n == 0:
        src.close()
        raise NotSupportedError(f"{p.suffix.lstrip('.')} (sin imágenes)")

    html_body, toc = _comic_html_streaming(n)
    return BookContent(
        html=html_body,
        toc=toc,
        title=p.stem,
        author="",
        total_pages=n,
        page_source=src,
    )
