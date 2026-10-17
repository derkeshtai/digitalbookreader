"""EPUB 2/3 parser with package-relative paths, spine, navigation, and covers."""
from __future__ import annotations

import base64
import hashlib
import mimetypes
import posixpath
import re
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

from ..app_paths import data_dir
from .html_utils import safe_id, sanitize_html

if TYPE_CHECKING:
    from . import BookContent

COVER_CACHE = data_dir() / "covers"
MAX_EMBEDDED_IMAGE_BYTES = 12 * 1024 * 1024


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(element: ET.Element, local: str) -> ET.Element | None:
    return next((child for child in element if _local(child.tag) == local), None)


def _descendants(element: ET.Element, local: str) -> list[ET.Element]:
    return [node for node in element.iter() if _local(node.tag) == local]


def _attr(element: ET.Element, local: str, default: str = "") -> str:
    for key, value in element.attrib.items():
        if _local(key) == local:
            return value
    return default


def _resolve_path(base_file: str, href: str) -> str | None:
    """Resolve an EPUB URI against a package file without escaping the archive."""
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc:
        return None
    relative = unquote(parsed.path)
    if not relative:
        return base_file
    if relative.startswith(("/", "\\")):
        resolved = posixpath.normpath(relative.lstrip("/\\"))
    else:
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(base_file), relative))
    if resolved in ("", ".", "..") or resolved.startswith(("../", "/")):
        return None
    return resolved


def _read_optional(archive: zipfile.ZipFile, name: str, names: set[str]) -> bytes | None:
    if name not in names:
        return None
    try:
        return archive.read(name)
    except (KeyError, OSError, RuntimeError, zipfile.BadZipFile):
        return None


def _collect_ids(raw: bytes, prefix: str) -> dict[str, str]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return {}
    result: dict[str, str] = {}
    for element in root.iter():
        for key, value in element.attrib.items():
            if _local(key) == "id" and value:
                result.setdefault(value, f"{prefix}--{safe_id(value)}")
    return result


def _fragment_anchor(section_id: str, fragment: str, id_maps: dict[str, dict[str, str]], path: str) -> str:
    mapped = id_maps.get(path, {}).get(unquote(fragment))
    return mapped or f"{section_id}--{safe_id(unquote(fragment))}"


class EpubParser:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.title = self.path.stem
        self.author = ""
        self.sections: list[str] = []
        self.toc: list[tuple[str, str]] = []
        self.cover_path: Path | None = None
        self._parse()

    def _parse(self) -> None:
        with zipfile.ZipFile(self.path) as archive:
            names = set(archive.namelist())
            opf_path = self._package_path(archive, names)
            opf_raw = _read_optional(archive, opf_path, names)
            if opf_raw is None:
                raise ValueError(f"El EPUB no contiene su paquete OPF: {opf_path}")
            try:
                package = ET.fromstring(opf_raw)
            except ET.ParseError as exc:
                raise ValueError("El paquete OPF del EPUB está dañado.") from exc

            metadata = _child(package, "metadata")
            if metadata is not None:
                titles = [" ".join(node.itertext()).strip() for node in _descendants(metadata, "title")]
                creators = [" ".join(node.itertext()).strip() for node in _descendants(metadata, "creator")]
                self.title = next((value for value in titles if value), self.title)
                self.author = ", ".join(value for value in creators if value)

            manifest_element = _child(package, "manifest")
            manifest: dict[str, dict[str, str]] = {}
            if manifest_element is not None:
                for item in manifest_element:
                    if _local(item.tag) != "item":
                        continue
                    item_id = item.get("id", "")
                    href = item.get("href", "")
                    if item_id and href:
                        manifest[item_id] = {
                            "href": href,
                            "media_type": item.get("media-type", "").lower(),
                            "properties": item.get("properties", "").lower(),
                        }

            spine_element = _child(package, "spine")
            spine_paths: list[tuple[str, str, bytes]] = []
            if spine_element is not None:
                for itemref in spine_element:
                    if _local(itemref.tag) != "itemref":
                        continue
                    item = manifest.get(itemref.get("idref", ""))
                    if not item or item["media_type"] not in {
                        "application/xhtml+xml", "text/html", "application/xml",
                    }:
                        continue
                    chapter_path = _resolve_path(opf_path, item["href"])
                    raw = _read_optional(archive, chapter_path or "", names)
                    if chapter_path and raw is not None:
                        spine_paths.append((chapter_path, item["href"], raw))

            section_ids = {path: f"chapter-{index}" for index, (path, _href, _raw) in enumerate(spine_paths, 1)}
            id_maps = {
                path: _collect_ids(raw, section_ids[path])
                for path, _href, raw in spine_paths
            }
            for chapter_path, _href, raw in spine_paths:
                section_id = section_ids[chapter_path]

                def resolve_resource(
                    tag: str,
                    attr: str,
                    value: str,
                    current_chapter: str = chapter_path,
                ) -> str | None:
                    parsed = urlsplit(value.strip())
                    if attr == "href" and parsed.scheme.lower() in {"http", "https", "mailto", "tel"}:
                        return value.strip()
                    if attr == "src" and parsed.scheme.lower() == "data":
                        if re.match(
                            r"^data:image/(?:png|jpeg|gif|webp|bmp);base64,",
                            value,
                            re.IGNORECASE,
                        ):
                            return value
                        return None
                    target_path = _resolve_path(current_chapter, value)
                    if not target_path:
                        return None
                    if attr == "href":
                        target_section = section_ids.get(target_path)
                        if not target_section:
                            return None
                        if parsed.fragment:
                            return "#" + _fragment_anchor(
                                target_section, parsed.fragment, id_maps, target_path)
                        return "#" + target_section
                    if attr == "src" and tag == "img":
                        try:
                            info = archive.getinfo(target_path)
                            if info.file_size > MAX_EMBEDDED_IMAGE_BYTES:
                                return None
                            data = archive.read(target_path)
                        except (KeyError, OSError, RuntimeError, zipfile.BadZipFile):
                            return None
                        mime = mimetypes.guess_type(target_path)[0]
                        if not mime or not mime.startswith("image/") or mime == "image/svg+xml":
                            return None
                        encoded = base64.b64encode(data).decode("ascii")
                        return f"data:{mime};base64,{encoded}"
                    return None

                safe_html, _chapter_title, _author, _toc, _text = sanitize_html(
                    raw,
                    resource_resolver=resolve_resource,
                    id_prefix=f"{section_id}--",
                    id_map=id_maps[chapter_path],
                )
                self.sections.append(f'<section id="{section_id}">{safe_html}</section>')

            self.toc = self._read_toc(archive, names, opf_path, manifest, section_ids, id_maps)
            self.cover_path = self._read_cover(archive, names, opf_path, metadata, manifest)

    def _package_path(self, archive: zipfile.ZipFile, names: set[str]) -> str:
        container_path = next((name for name in names if name.lower() == "meta-inf/container.xml"), None)
        if container_path:
            raw = _read_optional(archive, container_path, names)
            if raw:
                try:
                    container = ET.fromstring(raw)
                    rootfile = next(
                        (node for node in container.iter() if _local(node.tag) == "rootfile"), None)
                    full_path = rootfile.get("full-path", "") if rootfile is not None else ""
                    if full_path:
                        package_path = posixpath.normpath(unquote(full_path))
                        if package_path in names and not package_path.startswith("../"):
                            return package_path
                except ET.ParseError:
                    pass
        candidates = sorted(name for name in names if name.lower().endswith(".opf"))
        if len(candidates) == 1:
            return candidates[0]
        if "content.opf" in names:
            return "content.opf"
        raise ValueError("No se encontró el paquete OPF en META-INF/container.xml.")

    def _read_toc(
        self,
        archive: zipfile.ZipFile,
        names: set[str],
        opf_path: str,
        manifest: dict[str, dict[str, str]],
        section_ids: dict[str, str],
        id_maps: dict[str, dict[str, str]],
    ) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []

        def target_anchor(base_path: str, href: str) -> tuple[str, str] | None:
            target_path = _resolve_path(base_path, href)
            section_id = section_ids.get(target_path or "")
            if not target_path or not section_id:
                return None
            parsed = urlsplit(href)
            if parsed.fragment:
                return section_id, _fragment_anchor(section_id, parsed.fragment, id_maps, target_path)
            return section_id, section_id

        # EPUB 2 NCX navigation.
        for item in manifest.values():
            if item["media_type"] != "application/x-dtbnc+xml":
                continue
            ncx_path = _resolve_path(opf_path, item["href"])
            raw = _read_optional(archive, ncx_path or "", names)
            if not raw:
                continue
            try:
                ncx = ET.fromstring(raw)
            except ET.ParseError:
                continue
            for nav_point in _descendants(ncx, "navPoint"):
                label = next((node for node in nav_point.iter() if _local(node.tag) == "text"), None)
                content = next((node for node in nav_point if _local(node.tag) == "content"), None)
                title = " ".join(label.itertext()).strip() if label is not None else ""
                href = content.get("src", "") if content is not None else ""
                target = target_anchor(ncx_path or "", href)
                if title and target:
                    result.append((title, target[1]))

        # EPUB 3 navigation document. The anchor elements use the XHTML namespace;
        # epub:type="toc" is an attribute, not an element namespace.
        for item in manifest.values():
            if "nav" not in item["properties"].split() or item["media_type"] != "application/xhtml+xml":
                continue
            nav_path = _resolve_path(opf_path, item["href"])
            raw = _read_optional(archive, nav_path or "", names)
            if not raw:
                continue
            try:
                nav_doc = ET.fromstring(raw)
            except ET.ParseError:
                continue
            nav_elements = [node for node in nav_doc.iter() if _local(node.tag) == "nav"]
            toc_nav = next(
                (node for node in nav_elements if _attr(node, "type").lower().split() and "toc" in _attr(node, "type").lower().split()),
                nav_elements[0] if nav_elements else None,
            )
            if toc_nav is None:
                continue
            for anchor in _descendants(toc_nav, "a"):
                title = " ".join(anchor.itertext()).strip()
                href = _attr(anchor, "href")
                target = target_anchor(nav_path or "", href)
                if title and target:
                    result.append((title, target[1]))

        # Keep only the first occurrence of duplicate titles/anchors.
        return list(dict.fromkeys(result))

    def _read_cover(
        self,
        archive: zipfile.ZipFile,
        names: set[str],
        opf_path: str,
        metadata: ET.Element | None,
        manifest: dict[str, dict[str, str]],
    ) -> Path | None:
        cover_id = next(
            (item_id for item_id, item in manifest.items() if "cover-image" in item["properties"].split()),
            "",
        )
        if not cover_id and metadata is not None:
            for meta in _descendants(metadata, "meta"):
                if meta.get("name", "").lower() == "cover":
                    cover_id = meta.get("content", "")
                    break
        item = manifest.get(cover_id)
        if not item:
            return None
        cover_path = _resolve_path(opf_path, item["href"])
        raw = _read_optional(archive, cover_path or "", names)
        if not raw or len(raw) > MAX_EMBEDDED_IMAGE_BYTES:
            return None
        suffix = Path(urlsplit(item["href"]).path).suffix.lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
            suffix = ".img"
        digest = hashlib.sha256(raw).hexdigest()
        destination = COVER_CACHE / f"{digest}{suffix}"
        try:
            COVER_CACHE.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                destination.write_bytes(raw)
            return destination
        except OSError:
            return None


def parse_epub(path: str | Path) -> BookContent:
    from . import BookContent

    parser = EpubParser(path)
    return BookContent(
        html="\n".join(parser.sections) or "<p></p>",
        toc=parser.toc,
        title=parser.title,
        author=parser.author,
        cover_path=parser.cover_path,
        total_pages=max(1, len(parser.sections)),
    )
