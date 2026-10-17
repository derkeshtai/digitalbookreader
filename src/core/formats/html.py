"""HTML/XHTML document import with active content removed."""
from __future__ import annotations

import math
from pathlib import Path

from .html_utils import sanitize_html


def parse_html(path: str | Path):
    from . import BookContent

    p = Path(path)
    body, title, author, toc, text = sanitize_html(p.read_bytes(), base_dir=p.resolve().parent)
    return BookContent(
        html=body or "<p></p>",
        toc=toc,
        title=title or p.stem,
        author=author,
        total_pages=max(1, math.ceil(len(text) / 2000)),
        base_url=p.resolve().parent.as_uri() + "/",
    )
