"""Plain-text book import with BOM, legacy encoding, and CRLF paragraph support."""
from __future__ import annotations

import html
import math
import re
from pathlib import Path

from . import BookContent


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-32", "utf-16", "cp1250", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def parse_txt(path: str | Path) -> BookContent:
    p = Path(path)
    text = _decode(p.read_bytes()).replace("\r\n", "\n").replace("\r", "\n").strip("\ufeff\n \t")
    paragraphs = [part.strip() for part in re.split(r"\n[ \t]*\n+", text) if part.strip()]
    body = "\n".join(f"<p>{html.escape(par)}</p>" for par in paragraphs) or "<p></p>"
    return BookContent(
        html=body,
        toc=[],
        title=p.stem,
        author="",
        total_pages=max(1, math.ceil(len(text) / 2000)),
    )
