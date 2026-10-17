from __future__ import annotations

import base64
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from src.core.formats import NotSupportedError, parse
from src.core.formats.epub import EpubParser
from src.core.formats.fb2 import parse_fb2
from src.core.formats.html import parse_html
from src.core.formats.markdown import parse_markdown
from src.core.formats.odt import parse_odt
from src.core.formats.rtf import parse_rtf

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/"
    "9XcAAAAASUVORK5CYII="
)


class FormatParserTests(unittest.TestCase):
    def test_epub_resolves_namespaced_nested_package_and_spine_idref(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            book = root / "nested.epub"
            with zipfile.ZipFile(book, "w") as archive:
                archive.writestr("mimetype", "application/epub+zip")
                archive.writestr(
                    "META-INF/container.xml",
                    '''<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
                      <rootfiles><rootfile full-path="OPS/package.opf"/></rootfiles>
                    </container>''',
                )
                archive.writestr(
                    "OPS/package.opf",
                    '''<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
                      <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
                        <dc:title>Nested Test</dc:title><dc:creator>Ana Reader</dc:creator>
                      </metadata>
                      <manifest>
                        <item id="chapter" href="Text/chapter%201.xhtml" media-type="application/xhtml+xml"/>
                        <item id="toc" href="toc.ncx" media-type="application/x-dtbnc+xml"/>
                        <item id="cover" href="Images/cover.png" media-type="image/png" properties="cover-image"/>
                      </manifest>
                      <spine><itemref idref="chapter"/></spine>
                    </package>''',
                )
                archive.writestr(
                    "OPS/Text/chapter 1.xhtml",
                    '''<?xml version="1.0" encoding="UTF-8"?>
                    <html xmlns="http://www.w3.org/1999/xhtml"><head><title>Chapter</title></head>
                      <body><h1 id="opening">Opening</h1><p>Read <em>this</em>.</p>
                      <img src="../Images/cover.png" alt="cover"/>
                      <script>alert(1)</script></body></html>''',
                )
                archive.writestr(
                    "OPS/toc.ncx",
                    '''<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">
                      <navMap><navPoint id="n1"><navLabel><text>First chapter</text></navLabel>
                      <content src="Text/chapter%201.xhtml#opening"/></navPoint></navMap>
                    </ncx>''',
                )
                archive.writestr("OPS/Images/cover.png", PNG_1X1)

            cover_cache = root / "covers"
            with patch("src.core.formats.epub.COVER_CACHE", cover_cache):
                content = parse(book)
            self.assertEqual(content.title, "Nested Test")
            self.assertEqual(content.author, "Ana Reader")
            self.assertIn("Read", content.html)
            self.assertIn("this", content.html)
            self.assertIn("data:image/png;base64,", content.html)
            self.assertNotIn("<script", content.html.lower())
            self.assertEqual(content.toc, [("First chapter", "chapter-1--opening")])
            self.assertTrue(content.cover_path and content.cover_path.is_file())

    def test_epub3_nav_is_found_using_xhtml_navigation_markup(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = Path(tmp) / "nav.epub"
            with zipfile.ZipFile(book, "w") as archive:
                archive.writestr("mimetype", "application/epub+zip")
                archive.writestr(
                    "META-INF/container.xml",
                    '''<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
                      <rootfiles><rootfile full-path="OEBPS/book.opf"/></rootfiles>
                    </container>''',
                )
                archive.writestr(
                    "OEBPS/book.opf",
                    '''<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
                      <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Nav Test</dc:title></metadata>
                      <manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
                        <item id="c1" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest>
                      <spine><itemref idref="c1"/></spine></package>''',
                )
                archive.writestr(
                    "OEBPS/chapter.xhtml",
                    '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Chapter body</p></body></html>',
                )
                archive.writestr(
                    "OEBPS/nav.xhtml",
                    '''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
                      <body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">Start</a></li></ol></nav></body>
                    </html>''',
                )
            content = EpubParser(book)
            self.assertEqual(content.toc, [("Start", "chapter-1")])

    def test_fb2_reads_nested_metadata_and_body_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.fb2"
            path.write_text(
                '''<?xml version="1.0" encoding="UTF-8"?>
                <FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
                  <description><title-info><book-title>El libro</book-title>
                    <author><first-name>Ana</first-name><last-name>Ríos</last-name></author>
                  </title-info></description>
                  <body><section><title><p>Capítulo uno</p></title>
                    <p>Hola <emphasis>mundo</emphasis>.</p>
                    <section><title><p>Parte dos</p></title><p>Final.</p></section>
                  </section></body>
                </FictionBook>''',
                encoding="utf-8",
            )
            content = parse_fb2(path)
            self.assertEqual(content.title, "El libro")
            self.assertEqual(content.author, "Ana Ríos")
            self.assertIn("Hola <em>mundo</em>.", content.html)
            self.assertIn("Final.", content.html)
            self.assertEqual(content.total_pages, 2)
            self.assertEqual([title for title, _ in content.toc], ["Capítulo uno", "Parte dos"])

    def test_html_parser_removes_active_content_and_embeds_local_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cover.png").write_bytes(PNG_1X1)
            path = root / "book.html"
            path.write_text(
                '''<!doctype html><html><head><title>My HTML Book</title>
                <meta name="author" content="Reader"/><script>head attack()</script></head>
                <body><h1>Part one</h1><p onclick="evil()">Safe <strong>text</strong>.</p>
                <img src="cover.png" onerror="evil()"/><script>alert('x')</script>
                <a href="javascript:alert(1)">bad link</a></body></html>''',
                encoding="utf-8",
            )
            content = parse_html(path)
            self.assertEqual(content.title, "My HTML Book")
            self.assertEqual(content.author, "Reader")
            self.assertIn("Safe <strong>text</strong>.", content.html)
            self.assertIn("data:image/png;base64,", content.html)
            self.assertNotIn("script", content.html.lower())
            self.assertNotIn("onclick", content.html.lower())
            self.assertNotIn("javascript:", content.html.lower())
            self.assertEqual(content.toc, [("Part one", "heading-1")])

    def test_markdown_and_rtf_are_rendered_as_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            md = root / "book.md"
            md.write_text("# Chapter\n\nHello **reader**.\n\n<script>alert(1)</script>", encoding="utf-8")
            markdown_content = parse_markdown(md)
            self.assertIn("<strong>reader</strong>", markdown_content.html)
            self.assertNotIn("<script", markdown_content.html.lower())
            self.assertEqual(markdown_content.title, "Chapter")
            self.assertEqual(markdown_content.toc[0][0], "Chapter")

            rtf = root / "book.rtf"
            rtf.write_bytes(br"{\rtf1\ansi Hola \b mundo\b0\par Segundo p\'e1rrafo}")
            rtf_content = parse_rtf(rtf)
            self.assertIn("Hola mundo", rtf_content.html)
            self.assertIn("Segundo párrafo", rtf_content.html)

    def test_docx_metadata_headings_and_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book.docx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("[Content_Types].xml", "<Types/>")
                archive.writestr(
                    "word/document.xml",
                    '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
                      <w:body><w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Chapter</w:t></w:r></w:p>
                      <w:p><w:r><w:t>Hello DOCX</w:t></w:r></w:p>
                      <w:tbl><w:tr><w:tc><w:p><w:r><w:t>Cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
                      <w:sectPr/></w:body></w:document>''',
                )
                archive.writestr(
                    "docProps/core.xml",
                    '''<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
                      xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>DOCX title</dc:title><dc:creator>Ana</dc:creator></cp:coreProperties>''',
                )
            content = parse(path)
            self.assertEqual(content.title, "DOCX title")
            self.assertEqual(content.author, "Ana")
            self.assertIn("Hello DOCX", content.html)
            self.assertIn("<table>", content.html)
            self.assertEqual(content.toc[0][0], "Chapter")

    def test_odt_and_content_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book.odt"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("mimetype", "application/vnd.oasis.opendocument.text")
                archive.writestr(
                    "content.xml",
                    '''<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
                      xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">
                      <office:body><office:text><text:h text:outline-level="1">Open title</text:h>
                      <text:p>ODT <text:span>paragraph</text:span>.</text:p></office:text></office:body>
                    </office:document-content>''',
                )
                archive.writestr(
                    "meta.xml",
                    '''<office:document-meta xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
                      xmlns:dc="http://purl.org/dc/elements/1.1/"><office:meta><dc:title>ODT title</dc:title></office:meta></office:document-meta>''',
                )
            content = parse_odt(path)
            self.assertEqual(content.title, "ODT title")
            self.assertIn("ODT ", content.html)
            self.assertIn("<span>paragraph</span>", content.html)
            self.assertEqual(content.toc[0][0], "Open title")

    def test_pdf_and_comic_archives_render_pages_lazily(self):
        import py7zr
        import pymupdf

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf_path = root / "book.pdf"
            document = pymupdf.open()
            page = document.new_page()
            page.insert_text((72, 72), "PDF page")
            document.save(pdf_path)
            document.close()

            pdf_content = parse(pdf_path)
            self.assertEqual(pdf_content.total_pages, 1)
            image, mime = pdf_content.page_source.page_image(1)
            self.assertTrue(image)
            self.assertEqual(mime, "image/png")
            pdf_content.page_source.close()

            cbz_path = root / "comic.cbz"
            with zipfile.ZipFile(cbz_path, "w") as archive:
                archive.writestr("page10.png", PNG_1X1)
                archive.writestr("page2.png", PNG_1X1)
                archive.writestr("page1.png", PNG_1X1)
            comic_content = parse(cbz_path)
            self.assertEqual(comic_content.total_pages, 3)
            self.assertEqual(comic_content.page_source._names, ["page1.png", "page2.png", "page10.png"])
            first_page, mime = comic_content.page_source.page_image(1)
            self.assertEqual(first_page, PNG_1X1)
            self.assertEqual(mime, "image/png")
            self.assertIn('src="page/1"', comic_content.html)
            comic_content.page_source.close()

            seven_zip = root / "comic.7z"
            source_image = root / "image.png"
            source_image.write_bytes(PNG_1X1)
            source_image_two = root / "image-two.png"
            source_image_two.write_bytes(PNG_1X1)
            with py7zr.SevenZipFile(seven_zip, "w") as archive:
                archive.write(source_image, arcname="page1.png")
                archive.write(source_image_two, arcname="page2.png")
            seven_zip_content = parse(seven_zip)
            self.assertEqual(seven_zip_content.total_pages, 2)
            image, mime = seven_zip_content.page_source.page_image(1)
            self.assertEqual(image, PNG_1X1)
            self.assertEqual(mime, "image/png")
            image_two, mime_two = seven_zip_content.page_source.page_image(2)
            self.assertEqual(image_two, PNG_1X1)
            self.assertEqual(mime_two, "image/png")
            seven_zip_content.page_source.close()

    def test_extensionless_text_is_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book"
            path.write_text("First paragraph.\r\n\r\nSecond paragraph.", encoding="utf-8")
            content = parse(path)
            self.assertEqual(content.total_pages, 1)
            self.assertIn("<p>First paragraph.</p>", content.html)
            self.assertIn("<p>Second paragraph.</p>", content.html)

    def test_mobi_converter_result_is_cached_and_temporary_files_are_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "book.mobi"
            source.write_bytes(b"not a real mobi; converter is mocked")
            fake_module = types.SimpleNamespace()
            created: dict[str, Path] = {}

            def fake_extract(_source: str):
                temp_dir = Path(tempfile.mkdtemp(prefix="mock-mobi-"))
                output = temp_dir / "mobi7" / "book.html"
                output.parent.mkdir()
                output.write_text("<html><head><title>Converted</title></head><body><p>Text</p></body></html>", encoding="utf-8")
                created["temp"] = temp_dir
                return str(temp_dir), str(output)

            fake_module.extract = fake_extract
            with patch.dict(sys.modules, {"mobi": fake_module}), patch(
                "src.core.formats.mobi.cache_dir", lambda: root / "cache"
            ):
                content = parse(source)
            self.assertEqual(content.title, "Converted")
            self.assertIn("Text", content.html)
            self.assertFalse(created["temp"].exists())
            self.assertTrue((root / "cache" / "mobi").is_dir())

    def test_unknown_extension_is_only_rejected_when_content_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book.unknown"
            path.write_text("This is still readable text.", encoding="utf-8")
            self.assertIn("readable text", parse(path).html)
            binary = Path(tmp) / "binary.unknown"
            binary.write_bytes(b"\x00\x01\xff\x00\x01")
            with self.assertRaises(NotSupportedError):
                parse(binary)


if __name__ == "__main__":
    unittest.main()
