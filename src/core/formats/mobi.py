"""Unencrypted Mobipocket/Kindle import through the optional ``mobi`` converter."""
from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path

from ..app_paths import cache_dir


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _persist_extraction(source_dir: Path, output_path: Path, cache_root: Path) -> Path:
    try:
        relative = output_path.resolve().relative_to(source_dir.resolve())
    except ValueError as exc:
        raise ValueError("El conversor MOBI generó una ruta fuera de su carpeta temporal.") from exc

    cache_root.parent.mkdir(parents=True, exist_ok=True)
    if cache_root.exists() and not (cache_root / relative).is_file():
        shutil.rmtree(cache_root, ignore_errors=True)
    if not cache_root.exists():
        staging = Path(tempfile.mkdtemp(prefix="mobi-cache-", dir=cache_root.parent))
        shutil.rmtree(staging)
        try:
            shutil.copytree(source_dir, staging)
            try:
                staging.rename(cache_root)
            except OSError:
                if not cache_root.exists():
                    raise
                shutil.rmtree(staging, ignore_errors=True)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    persistent_output = cache_root / relative
    if not persistent_output.is_file():
        raise ValueError("No se encontró el libro convertido en la caché local.")
    return persistent_output


def parse_mobi(path: str | Path):
    """Read unencrypted MOBI/PRC/AZW/AZW3 books via KindleUnpack's Python package.

    The converter may return EPUB, HTML, or PDF. Its extraction is kept in the
    per-user cache because HTML resources and lazy PDF rendering need a stable path.
    DRM-protected Kindle books are not supported.
    """
    from . import BookContent

    p = Path(path)
    try:
        import mobi as mobi_converter
    except ImportError as exc:
        raise RuntimeError("Para leer MOBI/AZW instala las dependencias de requirements.txt.") from exc

    try:
        extracted_dir, output_file = mobi_converter.extract(str(p))
    except Exception as exc:  # converter reports malformed files and DRM protection here
        raise ValueError(f"No se pudo convertir el libro MOBI (puede tener DRM): {exc}") from exc

    temp_root = Path(extracted_dir)
    try:
        cache_root = cache_dir() / "mobi" / _sha256(p)
        persistent_output = _persist_extraction(temp_root, Path(output_file), cache_root)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    extension = persistent_output.suffix.lower()
    if extension == ".epub":
        from .epub import parse_epub
        content = parse_epub(persistent_output)
    elif extension in {".html", ".htm"}:
        from .html import parse_html
        content = parse_html(persistent_output)
    elif extension == ".pdf":
        from .pdf import parse_pdf
        content = parse_pdf(persistent_output)
    else:
        raise ValueError(f"El conversor MOBI produjo un formato no compatible: {extension or 'sin extensión'}")

    if content.title in {persistent_output.stem, "book", "Book"}:
        content.title = p.stem
    return BookContent(
        html=content.html,
        toc=content.toc,
        title=content.title or p.stem,
        author=content.author,
        cover_path=content.cover_path,
        total_pages=content.total_pages,
        base_url=content.base_url,
        page_source=content.page_source,
    )
