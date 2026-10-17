"""Microsoft Word DOCX text import (paragraphs, headings, tables, and images)."""
from __future__ import annotations

import base64
import html
import math
import mimetypes
import posixpath
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_DC = "{http://purl.org/dc/elements/1.1/}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(element: ET.Element, name: str) -> ET.Element | None:
    return next((child for child in element.iter() if _local(child.tag) == name), None)


def _plain_text(element: ET.Element) -> str:
    return "".join(node.text or "" for node in element.iter() if _local(node.tag) == "t").strip()


def _image_markup(element: ET.Element, relationships: dict[str, tuple[str, bool]], archive: zipfile.ZipFile) -> str:
    for blip in element.iter():
        if _local(blip.tag) != "blip":
            continue
        rid = blip.get(_R + "embed") or blip.get(_R + "link")
        rel = relationships.get(rid or "")
        if not rel or rel[1]:
            continue
        target = posixpath.normpath(posixpath.join("word", rel[0]))
        if target.startswith(("../", "/")):
            continue
        try:
            info = archive.getinfo(target)
            if info.file_size > 12 * 1024 * 1024:
                continue
            data = archive.read(target)
        except (KeyError, OSError, zipfile.BadZipFile):
            continue
        mime = mimetypes.guess_type(target)[0]
        if not mime or not mime.startswith("image/") or mime == "image/svg+xml":
            continue
        src = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
        return f'<img src="{src}" alt="" />'
    return ""


def _render_inline(element: ET.Element, relationships: dict[str, tuple[str, bool]], archive: zipfile.ZipFile) -> str:
    tag = _local(element.tag)
    if tag == "t":
        return html.escape(element.text or "")
    if tag in {"tab"}:
        return "&#9;"
    if tag in {"br", "cr"}:
        return "<br />"
    if tag == "drawing" or tag == "pict":
        return _image_markup(element, relationships, archive)
    if tag == "r":
        props = next((child for child in element if _local(child.tag) == "rPr"), None)
        content = "".join(
            _render_inline(child, relationships, archive)
            for child in element if _local(child.tag) != "rPr"
        )
        if props is not None:
            flags = {_local(child.tag) for child in props}
            if "b" in flags:
                content = f"<strong>{content}</strong>"
            if "i" in flags:
                content = f"<em>{content}</em>"
            if "strike" in flags or "dstrike" in flags:
                content = f"<s>{content}</s>"
            if "u" in flags:
                content = f"<u>{content}</u>"
        return content
    if tag == "hyperlink":
        content = "".join(_render_inline(child, relationships, archive) for child in element)
        anchor = element.get(_W + "anchor")
        if anchor:
            return f'<a href="#{html.escape(anchor, quote=True)}">{content}</a>'
        rel = relationships.get(element.get(_R + "id", ""))
        if rel and rel[1] and rel[0].lower().startswith(("http://", "https://", "mailto:")):
            return f'<a href="{html.escape(rel[0], quote=True)}">{content}</a>'
        return content
    if tag in {"pPr", "rPr", "sectPr", "bookmarkStart", "bookmarkEnd", "proofErr", "lastRenderedPageBreak"}:
        return ""
    content = html.escape(element.text or "")
    for child in element:
        content += _render_inline(child, relationships, archive)
        content += html.escape(child.tail or "")
    return content


def _paragraph(
    element: ET.Element,
    relationships: dict[str, tuple[str, bool]],
    archive: zipfile.ZipFile,
    heading_index: int = 1,
):
    style = next((node for node in element if _local(node.tag) == "pPr"), None)
    style_node = next((node for node in style or [] if _local(node.tag) == "pStyle"), None)
    style_name = (style_node.get(_W + "val", "") if style_node is not None else "")
    heading_level = None
    if style_name.lower().startswith("heading"):
        suffix = style_name[len("heading"):]
        if suffix.isdigit():
            heading_level = min(6, max(1, int(suffix)))
    text = _plain_text(element)
    rendered = "".join(_render_inline(child, relationships, archive) for child in element)
    if heading_level:
        anchor = f"heading-{heading_level}-{heading_index}"
        return f'<h{heading_level} id="{anchor}">{rendered}</h{heading_level}>', (text, anchor)
    return f"<p>{rendered}</p>", None


def parse_docx(path: str | Path):
    from . import BookContent

    p = Path(path)
    with zipfile.ZipFile(p) as archive:
        document = ET.fromstring(archive.read("word/document.xml"))
        rels: dict[str, tuple[str, bool]] = {}
        try:
            relroot = ET.fromstring(archive.read("word/_rels/document.xml.rels"))
            for rel in relroot:
                rels[rel.get("Id", "")] = (
                    rel.get("Target", ""), rel.get("TargetMode", "").lower() == "external")
        except KeyError:
            pass

        title, author = "", ""
        try:
            core = ET.fromstring(archive.read("docProps/core.xml"))
            title_node = next((n for n in core.iter() if n.tag == _DC + "title"), None)
            author_node = next((n for n in core.iter() if n.tag == _DC + "creator"), None)
            title = (title_node.text or "").strip() if title_node is not None else ""
            author = (author_node.text or "").strip() if author_node is not None else ""
        except (KeyError, ET.ParseError):
            pass

        body = _find(document, "body")
        rendered_parts: list[str] = []
        toc: list[tuple[str, str]] = []
        all_text: list[str] = []
        if body is not None:
            for child in body:
                tag = _local(child.tag)
                if tag == "p":
                    markup, heading = _paragraph(child, rels, archive, len(toc) + 1)
                    rendered_parts.append(markup)
                    text = _plain_text(child)
                    if text:
                        all_text.append(text)
                    if heading and heading[0]:
                        toc.append(heading)
                elif tag == "tbl":
                    rows: list[str] = []
                    for row in child:
                        if _local(row.tag) != "tr":
                            continue
                        cells: list[str] = []
                        for cell in row:
                            if _local(cell.tag) != "tc":
                                continue
                            cell_markup: list[str] = []
                            for paragraph in cell:
                                if _local(paragraph.tag) == "p":
                                    markup, _heading = _paragraph(paragraph, rels, archive)
                                    cell_markup.append(markup)
                                    text = _plain_text(paragraph)
                                    if text:
                                        all_text.append(text)
                            cells.append("<td>" + "".join(cell_markup) + "</td>")
                        rows.append("<tr>" + "".join(cells) + "</tr>")
                    if rows:
                        rendered_parts.append("<table><tbody>" + "".join(rows) + "</tbody></table>")

    text_length = sum(len(text) for text in all_text)
    return BookContent(
        html="\n".join(rendered_parts) or "<p></p>",
        toc=toc,
        title=title or p.stem,
        author=author,
        total_pages=max(1, math.ceil(text_length / 2000)),
    )
