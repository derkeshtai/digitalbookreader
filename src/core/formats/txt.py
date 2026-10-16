"""Plain text -> HTML (paragraphs), with encoding fallback for legacy files."""
import html
import math
from pathlib import Path

from . import BookContent


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "cp1250", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse_txt(path: str | Path) -> BookContent:
    p = Path(path)
    text = _decode(p.read_bytes()).strip()

    paragraphs = [html.escape(par.strip()) for par in text.split("\n\n") if par.strip()]
    body = "\n".join(f"<p>{par}</p>" for par in paragraphs) or "<p></p>"

    # Rough page estimate (~2000 chars per rendered page).
    total_pages = max(1, math.ceil(len(text) / 2000))

    return BookContent(
        html=body,
        toc=[],
        title=p.stem,
        author="",
        total_pages=total_pages,
    )
