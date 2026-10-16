"""FictionBook 2.0 (FB2) -> HTML via a namespace-agnostic XML walk."""
import html
from pathlib import Path
from xml.etree import ElementTree as ET


def _local_children(el: ET.Element, local: str) -> list[ET.Element]:
    return [c for c in el if c.tag.split("}")[-1] == local]


def _descendants(el: ET.Element, local: str) -> list[ET.Element]:
    out = []
    for e in el.iter():
        if e.tag.split("}")[-1] == local:
            out.append(e)
    return out


def _text(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return "".join(t for t in el.itertext() if isinstance(t, str)).strip()


def parse_fb2(path: str | Path) -> "BookContent":
    from . import BookContent

    p = Path(path)
    root = ET.parse(str(p)).getroot()

    title_info = _local_children(root, "title_info")[0] if _local_children(root, "title_info") else None
    title_el = _local_children(title_info or ET.Element(""), "title")[0] \
        if _local_children(title_info or ET.Element(""), "title") else None
    author_el = _local_children(title_info or ET.Element(""), "author")[0] \
        if _local_children(title_info or ET.Element(""), "author") else None

    title = _text(title_el) or p.stem
    author = _text(author_el)

    sections: list[str] = []
    toc: list[tuple[str, str]] = []
    for i, sec in enumerate(_local_children(root, "section")):
        sec_title_el = _local_children(sec, "title")[0] if _local_children(sec, "title") else None
        text_el = _local_children(sec, "text")[0] if _local_children(sec, "text") else None
        body_parts = [_text(par) for par in _descendants(text_el or ET.Element(""), "p")]
        body = "\n".join(f"<p>{html.escape(part)}</p>" for part in body_parts if part)
        sid = f"sec{i}"
        sections.append(f'<section id="{sid}">{body}</section>')
        sec_title = _text(sec_title_el)
        if sec_title:
            toc.append((sec_title, sid))

    return BookContent(
        html="\n".join(sections) or "<p></p>",
        toc=toc,
        title=title,
        author=author,
        total_pages=max(1, len(sections)),
    )
