"""Rich Text Format import, converted into simple paragraph HTML."""
from __future__ import annotations

import html
import math
from pathlib import Path


def parse_rtf(path: str | Path):
    from . import BookContent

    p = Path(path)
    try:
        from striprtf.striprtf import rtf_to_text
    except ImportError as exc:
        raise RuntimeError("Para leer RTF instala las dependencias de requirements.txt.") from exc

    raw = p.read_bytes()
    # Latin-1 preserves every source byte; striprtf handles the declared RTF codepage.
    text = rtf_to_text(raw.decode("latin-1"), encoding="cp1252", errors="replace").strip()
    paragraphs = [line.strip() for line in text.splitlines() if line.strip()]
    body = "\n".join(f"<p>{html.escape(line)}</p>" for line in paragraphs) or "<p></p>"
    return BookContent(
        html=body,
        toc=[],
        title=p.stem,
        author="",
        total_pages=max(1, math.ceil(len(text) / 2000)),
    )
