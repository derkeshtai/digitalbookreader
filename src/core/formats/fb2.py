"""FictionBook 2 (FB2) parser with namespace-agnostic metadata and body traversal."""
from __future__ import annotations

import base64
import html
import math
import re
from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

if TYPE_CHECKING:
    from . import BookContent


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(element: ET.Element, local: str) -> list[ET.Element]:
    return [child for child in element if _local(child.tag) == local]


def _first_descendant(element: ET.Element, local: str) -> ET.Element | None:
    return next((item for item in element.iter() if _local(item.tag) == local), None)


def _text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return " ".join(part.strip() for part in element.itertext() if part.strip())


def _attribute(element: ET.Element, local: str) -> str:
    for key, value in element.attrib.items():
        if _local(key) == local:
            return value
    return ""


def _safe_anchor(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.:-]+", "-", value.strip()).strip("-.")[:160]


def _render_inline(element: ET.Element, binaries: dict[str, str]) -> str:
    tag = _local(element.tag)
    if tag == "image":
        href = _attribute(element, "href").lstrip("#")
        src = binaries.get(href)
        return f'<img src="{src}" alt="" />' if src else ""
    if tag in {"empty-line"}:
        return "<br />"
    markup = {
        "emphasis": "em", "strong": "strong", "strikethrough": "s",
        "sub": "sub", "sup": "sup", "code": "code",
    }.get(tag)
    content = html.escape(element.text or "")
    for child in element:
        content += _render_inline(child, binaries)
        content += html.escape(child.tail or "")
    if tag == "a":
        href = _attribute(element, "href")
        if href.startswith("#"):
            return f'<a href="#{_safe_anchor(href[1:])}">{content}</a>'
        if href.lower().startswith(("https://", "http://", "mailto:")):
            return f'<a href="{html.escape(href, quote=True)}">{content}</a>'
        return content
    return f"<{markup}>{content}</{markup}>" if markup else content


def _render_block(element: ET.Element, binaries: dict[str, str]) -> str:
    tag = _local(element.tag)
    if tag == "section":
        return ""
    if tag == "image":
        return _render_inline(element, binaries)
    if tag == "empty-line":
        return "<br />"
    content = html.escape(element.text or "")
    for child in element:
        if _local(child.tag) == "section":
            continue
        content += _render_inline(child, binaries)
        content += html.escape(child.tail or "")
    if tag == "title":
        return f"<h2>{content}</h2>"
    if tag == "subtitle":
        return f"<h3>{content}</h3>"
    if tag in {"poem", "epigraph", "cite"}:
        return f"<blockquote>{content}</blockquote>"
    if tag == "stanza":
        return f'<div class="stanza">{content}</div>'
    if tag in {"p", "v", "text-author", "date"}:
        return f"<p>{content}</p>"
    return content


def parse_fb2(path: str | Path) -> BookContent:
    from . import BookContent

    p = Path(path)
    root = ET.parse(str(p)).getroot()
    description = _first_descendant(root, "description")
    title_info = _first_descendant(description, "title-info") if description is not None else None
    title = _text(_first_descendant(title_info, "book-title")) if title_info is not None else ""
    authors: list[str] = []
    if title_info is not None:
        for author_el in _children(title_info, "author"):
            parts = []
            for name in ("first-name", "middle-name", "last-name", "nickname"):
                value = _text(next(iter(_children(author_el, name)), None))
                if value:
                    parts.append(value)
            if parts:
                authors.append(" ".join(parts))
    author = ", ".join(authors)

    binaries: dict[str, str] = {}
    for binary in _children(root, "binary"):
        binary_id = binary.get("id", "")
        content_type = binary.get("content-type", "application/octet-stream").lower()
        if not binary_id or not content_type.startswith("image/") or content_type == "image/svg+xml":
            continue
        try:
            data = base64.b64decode("".join((binary.text or "").split()), validate=False)
        except (ValueError, base64.binascii.Error):
            continue
        if len(data) <= 12 * 1024 * 1024:
            binaries[binary_id] = f"data:{content_type};base64,{base64.b64encode(data).decode('ascii')}"

    bodies = _children(root, "body")
    sections: list[str] = []
    toc: list[tuple[str, str]] = []
    text_length = 0
    section_number = 0

    def render_section(section: ET.Element) -> str:
        nonlocal section_number, text_length
        section_number += 1
        section_id = f"fb2-section-{section_number}"
        section_title = _text(next(iter(_children(section, "title")), None))
        if section_title:
            toc.append((section_title, section_id))
        parts = [f'<section id="{section_id}">']
        for child in section:
            if _local(child.tag) == "section":
                parts.append(render_section(child))
                continue
            parts.append(_render_block(child, binaries))
            text_length += len(_text(child))
        parts.append("</section>")
        return "".join(parts)

    for body in bodies:
        for child in body:
            if _local(child.tag) == "section":
                sections.append(render_section(child))
            else:
                sections.append(_render_block(child, binaries))
                text_length += len(_text(child))

    body_html = "\n".join(section for section in sections if section) or "<p></p>"
    return BookContent(
        html=body_html,
        toc=toc,
        title=title or p.stem,
        author=author,
        total_pages=max(1, section_number or math.ceil(text_length / 2000)),
    )
