"""OpenDocument Text (ODT) import for paragraphs, headings, lists, and tables."""
from __future__ import annotations

import base64
import html
import math
import mimetypes
import posixpath
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _attr(element: ET.Element, local_name: str, default: str = "") -> str:
    for name, value in element.attrib.items():
        if _local(name) == local_name:
            return value
    return default


def _image(element: ET.Element, archive: zipfile.ZipFile) -> str:
    href = _attr(element, "href")
    if not href or ":" in href or href.startswith(("/", "\\")):
        return ""
    target = posixpath.normpath(href)
    if target.startswith("../"):
        return ""
    try:
        info = archive.getinfo(target)
        if info.file_size > 12 * 1024 * 1024:
            return ""
        data = archive.read(target)
    except (KeyError, OSError, zipfile.BadZipFile):
        return ""
    mime = mimetypes.guess_type(target)[0]
    if not mime or not mime.startswith("image/") or mime == "image/svg+xml":
        return ""
    encoded = base64.b64encode(data).decode("ascii")
    return f'<img src="data:{mime};base64,{encoded}" alt="" />'


def _inline(element: ET.Element, archive: zipfile.ZipFile) -> str:
    tag = _local(element.tag)
    if tag == "s":
        try:
            count = min(100, max(1, int(_attr(element, "c", "1"))))
        except ValueError:
            count = 1
        return " " * count
    if tag == "tab":
        return "&#9;"
    if tag in {"line-break", "soft-page-break"}:
        return "<br />"
    if tag == "image":
        return _image(element, archive)
    if tag == "bookmark-start":
        name = _attr(element, "name")
        return f'<span id="{html.escape(name, quote=True)}"></span>' if name else ""
    if tag in {"note", "note-citation", "note-body", "annotation"}:
        return ""
    content = html.escape(element.text or "")
    for child in element:
        content += _inline(child, archive)
        content += html.escape(child.tail or "")
    if tag == "a":
        href = _attr(element, "href")
        if href.startswith(("http://", "https://", "mailto:", "#")):
            return f'<a href="{html.escape(href, quote=True)}">{content}</a>'
        return content
    if tag == "span":
        return f"<span>{content}</span>"
    return content


def _blocks(element: ET.Element, archive: zipfile.ZipFile, toc: list[tuple[str, str]], text_parts: list[str]) -> str:
    rendered: list[str] = []
    for child in element:
        tag = _local(child.tag)
        if tag in {"p", "h"}:
            content = _inline(child, archive)
            text = " ".join((node.text or "") for node in child.iter() if node.text).strip()
            text_parts.append(text)
            if tag == "h":
                level_value = _attr(child, "outline-level", "1")
                try:
                    level = min(6, max(1, int(level_value)))
                except ValueError:
                    level = 1
                anchor = f"odt-heading-{len(toc) + 1}"
                rendered.append(f'<h{level} id="{anchor}">{content}</h{level}>' )
                if text:
                    toc.append((text, anchor))
            else:
                rendered.append(f"<p>{content}</p>")
        elif tag == "list":
            items: list[str] = []
            for item in child:
                if _local(item.tag) == "list-item":
                    items.append("<li>" + _blocks(item, archive, toc, text_parts) + "</li>")
            rendered.append("<ul>" + "".join(items) + "</ul>")
        elif tag == "table":
            rows: list[str] = []
            for row in child:
                if _local(row.tag) not in {"table-row", "table-header-rows"}:
                    continue
                row_items = list(row) if _local(row.tag) == "table-header-rows" else [row]
                for actual_row in row_items:
                    if _local(actual_row.tag) != "table-row":
                        continue
                    cells: list[str] = []
                    for cell in actual_row:
                        if _local(cell.tag) not in {"table-cell", "covered-table-cell"}:
                            continue
                        cells.append("<td>" + _blocks(cell, archive, toc, text_parts) + "</td>")
                    rows.append("<tr>" + "".join(cells) + "</tr>")
            if rows:
                rendered.append("<table><tbody>" + "".join(rows) + "</tbody></table>")
        elif tag in {"section", "text", "list-item", "table-cell", "covered-table-cell"}:
            rendered.append(_blocks(child, archive, toc, text_parts))
        elif tag == "frame":
            for image in child.iter():
                if _local(image.tag) == "image":
                    rendered.append(_image(image, archive))
        else:
            # Preserve ordinary nested paragraphs in less common ODT containers.
            if any(_local(desc.tag) in {"p", "h", "list", "table"} for desc in child.iter()):
                rendered.append(_blocks(child, archive, toc, text_parts))
    return "".join(rendered)


def parse_odt(path: str | Path):
    from . import BookContent

    p = Path(path)
    with zipfile.ZipFile(p) as archive:
        document = ET.fromstring(archive.read("content.xml"))
        body = next((node for node in document.iter() if _local(node.tag) == "text"), None)
        toc: list[tuple[str, str]] = []
        text_parts: list[str] = []
        content = _blocks(body, archive, toc, text_parts) if body is not None else ""

        title, author = "", ""
        try:
            meta = ET.fromstring(archive.read("meta.xml"))
            for node in meta.iter():
                local = _local(node.tag)
                if local == "title" and not title:
                    title = (node.text or "").strip()
                elif local in {"initial-creator", "creator"} and not author:
                    author = (node.text or "").strip()
        except (KeyError, ET.ParseError):
            pass

    text = " ".join(part for part in text_parts if part)
    return BookContent(
        html=content or "<p></p>",
        toc=toc,
        title=title or p.stem,
        author=author,
        total_pages=max(1, math.ceil(len(text) / 2000)),
    )
