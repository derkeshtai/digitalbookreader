"""Markdown book import rendered to safe HTML."""
from __future__ import annotations

import math
from pathlib import Path

from .html_utils import sanitize_html


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse_markdown(path: str | Path):
    from . import BookContent

    p = Path(path)
    try:
        import markdown as markdown_lib
    except ImportError as exc:
        raise RuntimeError("Para leer Markdown instala las dependencias de requirements.txt.") from exc

    rendered = markdown_lib.markdown(
        _decode(p.read_bytes()),
        extensions=["extra", "toc", "sane_lists"],
        output_format="html5",
    )
    body, _title, _author, toc, text = sanitize_html(
        rendered, base_dir=p.resolve().parent)
    first_heading = toc[0][0] if toc else ""
    return BookContent(
        html=body or "<p></p>",
        toc=toc,
        title=first_heading or p.stem,
        author="",
        total_pages=max(1, math.ceil(len(text) / 2000)),
        base_url=p.resolve().parent.as_uri() + "/",
    )
