"""Small HTML sanitization and metadata helpers for imported book content.

The reader renders imported documents in an embedded browser. This module keeps
only ordinary text/formatting markup, strips active content, and allows parsers to
resolve document-local resources without trusting arbitrary URLs from a book.
"""
from __future__ import annotations

import base64
import html
import mimetypes
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import unquote, urlsplit

_ALLOWED_TAGS = {
    "a", "article", "aside", "b", "blockquote", "br", "caption", "cite",
    "code", "dd", "del", "div", "dl", "dt", "em", "figcaption", "figure",
    "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "li", "main",
    "ol", "p", "pre", "q", "s", "section", "small", "span", "strong", "sub",
    "sup", "table", "tbody", "td", "th", "thead", "tr", "u", "ul",
}
_VOID_TAGS = {"br", "hr", "img"}
_BLOCKED_TAGS = {
    "applet", "audio", "base", "button", "canvas", "embed", "form", "iframe",
    "input", "link", "math", "meta", "object", "script", "select", "style",
    "svg", "textarea", "video",
}
_SAFE_ATTRS = {
    "alt", "colspan", "dir", "height", "lang", "rowspan", "scope", "start",
    "title", "width",
}
_SAFE_STYLE_PROPERTIES = {
    "background-color", "color", "direction", "font-family", "font-size",
    "font-style", "font-weight", "line-height", "margin-left", "margin-right",
    "text-align", "text-decoration", "text-indent", "vertical-align", "white-space",
}
_ID_RE = re.compile(r"[^A-Za-z0-9_.:-]+")
_IMAGE_DATA_RE = re.compile(r"^data:image/(?:png|jpeg|gif|webp|bmp);base64,", re.I)

ResourceResolver = Callable[[str, str, str], Optional[str]]


def safe_id(value: str) -> str:
    """Return a stable, harmless HTML id derived from arbitrary source text."""
    value = _ID_RE.sub("-", value.strip()).strip("-.")
    return value[:160] or "section"


def _safe_style(value: str) -> str:
    """Keep a conservative subset of inline CSS declarations."""
    kept: list[str] = []
    for declaration in value.split(";"):
        if ":" not in declaration:
            continue
        name, raw_value = declaration.split(":", 1)
        name, raw_value = name.strip().lower(), raw_value.strip()
        if name not in _SAFE_STYLE_PROPERTIES:
            continue
        if re.search(r"url\s*\(|expression|javascript:|@import|[<>]", raw_value, re.I):
            continue
        if not re.fullmatch(r"[\w\s#.,%()+/\-]+", raw_value):
            continue
        kept.append(f"{name}:{raw_value}")
    return ";".join(kept)


def _embed_local_image(base_dir: Optional[Path], value: str) -> Optional[str]:
    """Embed a small image from beside a standalone HTML/Markdown document."""
    if not base_dir:
        return None
    parsed = urlsplit(value.strip())
    if "\\" in parsed.path or parsed.scheme or parsed.netloc or not parsed.path or parsed.path.startswith(("/", "\\")):
        return None
    parts = Path(unquote(parsed.path)).parts
    if any(part in ("..", "") for part in parts):
        return None
    root = base_dir.resolve()
    candidate = (root / Path(*parts)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    try:
        if not candidate.is_file() or candidate.stat().st_size > 12 * 1024 * 1024:
            return None
        data = candidate.read_bytes()
    except OSError:
        return None
    mime = mimetypes.guess_type(candidate.name)[0]
    if not mime or not mime.startswith("image/") or mime == "image/svg+xml":
        return None
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _default_resource(tag: str, attr: str, value: str, base_dir: Optional[Path]) -> Optional[str]:
    value = value.strip()
    if not value or value.startswith("//"):
        return None
    parsed = urlsplit(value)
    scheme = parsed.scheme.lower()
    if attr == "src":
        if _IMAGE_DATA_RE.match(value):
            return value
        if scheme:
            return None
        return _embed_local_image(base_dir, value)
    if attr == "href":
        if scheme in {"http", "https", "mailto", "tel"}:
            return value
        if scheme:
            return None
        if value.startswith("#"):
            return value
        # Keep relative navigation within the selected document's directory.
        if parsed.path and "\\" not in parsed.path and not any(part == ".." for part in Path(unquote(parsed.path)).parts):
            return value
    return None


class _SafeHTMLParser(HTMLParser):
    def __init__(
        self,
        *,
        resource_resolver: Optional[ResourceResolver] = None,
        base_dir: Optional[Path] = None,
        id_prefix: str = "",
        id_map: Optional[dict[str, str]] = None,
    ) -> None:
        super().__init__(convert_charrefs=True)
        self.resource_resolver = resource_resolver
        self.base_dir = base_dir
        self.id_prefix = id_prefix
        self.id_map = id_map or {}
        self.output: list[str] = []
        self.text_parts: list[str] = []
        self.open_tags: list[str] = []
        self.skip_depth = 0
        self.in_head = False
        self.in_title = False
        self.body_depth = 0
        self.saw_body = False
        self.title_parts: list[str] = []
        self.author = ""
        self.toc: list[tuple[str, str]] = []
        self._heading: Optional[dict[str, object]] = None
        self._heading_count = 0

    def _resource(self, tag: str, attr: str, value: str) -> Optional[str]:
        if self.resource_resolver:
            return self.resource_resolver(tag, attr, value)
        return _default_resource(tag, attr, value, self.base_dir)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        attr_map = {name.lower(): (value or "") for name, value in attrs if name}

        if tag == "meta":
            if attr_map.get("name", "").strip().lower() == "author":
                self.author = attr_map.get("content", "").strip()
            return
        if tag == "head":
            self.in_head = True
            return
        if tag == "body":
            self.saw_body = True
            self.body_depth += 1
            return
        if tag == "title":
            if self.in_head:
                self.in_title = True
            return

        if self.skip_depth:
            if tag not in _VOID_TAGS:
                self.skip_depth += 1
            return
        if tag in _BLOCKED_TAGS:
            self.skip_depth = 1
            return
        if self.in_head or (self.saw_body and self.body_depth == 0):
            return
        if tag not in _ALLOWED_TAGS:
            return

        safe_attrs: list[tuple[str, str]] = []
        for name, value in attrs:
            if not name or value is None:
                continue
            name = name.lower()
            value = value.strip()
            if name.startswith("on") or name in {"xmlns", "srcset", "form", "action"}:
                continue
            if name == "id":
                value = self.id_map.get(value, f"{self.id_prefix}{safe_id(value)}")
            elif name == "class":
                value = re.sub(r"[^A-Za-z0-9_ -]", "", value)[:300]
            elif name == "style":
                value = _safe_style(value)
                if not value:
                    continue
            elif name in {"href", "src"}:
                if name == "href" and value.startswith("#"):
                    fragment = unquote(value[1:])
                    value = "#" + self.id_map.get(fragment, f"{self.id_prefix}{safe_id(fragment)}")
                else:
                    resolved = self._resource(tag, name, value)
                    if resolved is None:
                        continue
                    value = resolved
            elif name not in _SAFE_ATTRS:
                continue
            safe_attrs.append((name, value))

        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            attrs_by_name = dict(safe_attrs)
            if not attrs_by_name.get("id"):
                self._heading_count += 1
                generated = f"heading-{self._heading_count}"
                safe_attrs.append(("id", generated))
                attrs_by_name["id"] = generated
            self._heading = {"parts": [], "id": attrs_by_name.get("id", ""), "tag": tag}

        self.output.append("<" + tag)
        for name, value in safe_attrs:
            self.output.append(f' {name}="{html.escape(value, quote=True)}"')
        self.output.append(">")
        if tag not in _VOID_TAGS:
            self.open_tags.append(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.skip_depth:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if tag == "head":
            self.in_head = False
            self.in_title = False
            return
        if tag == "title":
            self.in_title = False
            return
        if tag == "body":
            self.body_depth = max(0, self.body_depth - 1)
            return
        if self.in_head or (self.saw_body and self.body_depth == 0):
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"} and self._heading is not None:
            parts = self._heading["parts"]
            title = " ".join(str(part).strip() for part in parts if str(part).strip())
            if title:
                self.toc.append((title, str(self._heading["id"] or "")))
            self._heading = None
        if tag not in self.open_tags:
            return
        # Close malformed/nested HTML in a deterministic order.
        while self.open_tags:
            opened = self.open_tags.pop()
            self.output.append(f"</{opened}>")
            if opened == tag:
                break

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if self.in_title:
            self.title_parts.append(data)
            return
        if self.in_head or (self.saw_body and self.body_depth == 0):
            return
        self.text_parts.append(data)
        if self._heading is not None:
            self._heading["parts"].append(data)
        self.output.append(html.escape(data, quote=False))

    def finish(self) -> None:
        self.close()
        while self.open_tags:
            self.output.append(f"</{self.open_tags.pop()}>")


def _decode_html(content: bytes) -> str:
    if content.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return content.decode("utf-32", errors="replace")
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        return content.decode("utf-16", errors="replace")
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def sanitize_html(
    content: str | bytes,
    *,
    resource_resolver: Optional[ResourceResolver] = None,
    base_dir: Optional[Path] = None,
    id_prefix: str = "",
    id_map: Optional[dict[str, str]] = None,
) -> tuple[str, str, str, list[tuple[str, str]], str]:
    """Return sanitized HTML, title, author, heading TOC, and extracted text."""
    if isinstance(content, bytes):
        content = _decode_html(content)
    parser = _SafeHTMLParser(
        resource_resolver=resource_resolver,
        base_dir=base_dir,
        id_prefix=id_prefix,
        id_map=id_map,
    )
    parser.feed(content)
    parser.finish()
    title = " ".join(part.strip() for part in parser.title_parts if part.strip())
    plain_text = " ".join(part.strip() for part in parser.text_parts if part.strip())
    return "".join(parser.output), title, parser.author, parser.toc, plain_text
