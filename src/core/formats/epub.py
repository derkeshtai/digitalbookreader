"""EPUB -> HTML with a self-contained parser (OPF, manifest, spine, TOC).

Reads the container to find the OPF, walks the spine in order into
<section>s, and builds anchor-based TOC entries from NCX or EPUB3 nav
documents.
"""
import hashlib
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS_DC = "{http://purl.org/dc/elements/1.1/}"
NS_NCX_POINT = "{http://www.w3.org/2007/OPF/ns}navPoint"
NS_NAV_A = "{http://www.w3.org/ns/dpub/2014/navigation}a"


def _child(el: ET.Element, local: str) -> ET.Element | None:
    """First child of el whose local tag name matches (namespace-agnostic)."""
    for c in el:
        if c.tag.split("}")[-1] == local:
            return c
    return None

COVER_CACHE = Path.home() / "AppData" / "Local" / "Digital Book Reader" / "covers"


def _norm(href: str) -> str:
    """Normalize an EPUB href into a stable section id."""
    h = href.split("#")[0].strip().lower()
    for ext in (".html", ".xhtml", ".htm"):
        if h.endswith(ext):
            h = h[: -len(ext)]
    return h.strip("/")


class EpubParser:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        with zipfile.ZipFile(self.path) as z:
            names = set(z.namelist())
            try:
                croot = ET.fromstring(z.read("META-INF/container.xml"))
                rf = croot.find("{urn:oasis:names:tc:opendocument:xmlns:container}rootfiles/rootfile")
                opf_path = (rf.get("full-path") if rf is not None else "content.opf").strip()
            except Exception:
                opf_path = "content.opf"

            self._files = {n: z.read(n) for n in names if not n.startswith("META-INF/")}
            root = ET.fromstring(self._files[opf_path])

            # -- metadata -------------------------------------------------------
            meta: dict[str, str] = {}
            for el in root.iter():
                tag = el.tag.split("}")[-1]
                if tag in ("title", "creator", "author") and tag not in meta:
                    txt = "".join(t for t in el.itertext() if isinstance(t, str)).strip()
                    if txt:
                        meta[tag] = txt
            self.title = meta.get("title") or self.path.stem
            self.author = meta.get("creator") or meta.get("author") or ""

            # -- manifest + spine (namespace-agnostic) ---------------------------
            manifest_el = _child(root, "manifest")
            manifest: dict[str, str] = {}
            if manifest_el is not None:
                for i in manifest_el:
                    if i.tag.split("}")[-1] == "item":
                        manifest[i.get("id")] = i.get("href")
            spine_el = _child(root, "spine")
            spine_ids: list[str] = []
            if spine_el is not None:
                for s in spine_el:
                    if s.tag.split("}")[-1] == "itemref":
                        spine_ids.append(s.get("id"))

            self.sections: list[str] = []
            self.ids: set[str] = set()
            for sid in spine_ids:
                href = manifest.get(sid)
                if not href or href not in self._files:
                    continue
                body = self._files[href].decode("utf-8", errors="replace")
                sec_id = _norm(href) or f"sec{len(self.sections)}"
                self.ids.add(sec_id)
                self.sections.append(f'<section id="{sec_id}">{body}</section>')

            # -- cover (dc:source itemref) ------------------------------------------
            self.cover_path: Path | None = None
            src = root.find(NS_DC + "source")
            if src is not None and src.get("id") in manifest:
                href = manifest[src.get("id")]
                if href in self._files:
                    COVER_CACHE.mkdir(parents=True, exist_ok=True)
                    name = f"{hashlib.md5(self._files[href]).hexdigest()}{Path(href).suffix}"
                    self.cover_path = COVER_CACHE / name
                    self.cover_path.write_bytes(self._files[href])

            # -- TOC: NCX (EPUB2) or nav document (EPUB3) ---------------------------
            self.toc: list[tuple[str, str]] = []
            for item in manifest_el if manifest_el is not None else []:
                if item.tag.split("}")[-1] != "item":
                    continue
                href = item.get("href")
                if not href or href not in self._files:
                    continue
                media_type = item.get("media-type", "")
                if media_type == "application/x-dtbnc+xml":
                    nroot = ET.fromstring(self._files[href])
                    for np in nroot.findall(NS_NCX_POINT):
                        txt_el = np.find("{http://www.w3.org/2007/OPF/ns}text")
                        content_el = np.find("{http://www.w3.org/2007/OPF/ns}content")
                        title = (txt_el.text or "").strip() if txt_el is not None else ""
                        src_href = content_el.get("src", "") if content_el is not None else ""
                        tid = _norm(src_href)
                        if tid and tid in self.ids:
                            self.toc.append((title, tid))
                elif media_type == "application/xhtml+xml":
                    nroot = ET.fromstring(self._files[href])
                    for a in nroot.findall(NS_NAV_A):
                        title = (a.text or "").strip()
                        tid = _norm(a.get("href", ""))
                        if tid and tid in self.ids:
                            self.toc.append((title, tid))


def parse_epub(path: str | Path) -> "BookContent":
    from . import BookContent

    p = EpubParser(path)
    return BookContent(
        html="\n".join(p.sections) or "<p></p>",
        toc=p.toc,
        title=p.title,
        author=p.author,
        cover_path=p.cover_path,
        total_pages=max(1, len(p.sections)),
    )
