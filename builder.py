from __future__ import annotations

import hashlib
import html
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from cleaner import ChapterContent, CleanedDocument, FlowImageUnit, FlowNoteUnit, FlowTableUnit, FlowTextUnit
from config import DEFAULT_LANGUAGE, DEFAULT_TITLE
from cover import COVER_IMAGE, TitlePage, synthesize_cover_svg
from inline import (
    EM_CLOSE,
    EM_OPEN,
    NOTE_RE,
    SENTINEL_RE,
    STRONG_CLOSE,
    STRONG_OPEN,
    SUB_CLOSE,
    SUB_OPEN,
    SUP_CLOSE,
    SUP_OPEN,
)


EPUB_CSS = """@charset "utf-8";

html, body {
  margin: 0;
  padding: 0;
  background: #fff;
  color: #111;
  font-family: Georgia, "Times New Roman", serif;
  line-height: 1.45;
  text-rendering: optimizeLegibility;
}

body {
  margin: 5%;
}

p {
  margin: 0;
  text-indent: 1.5em;
  text-align: justify;
  hyphens: auto;
}

p.scene-break { margin: 1.5em 0; text-align: center; text-indent: 0; letter-spacing: 0.2em; }

h1, h2, h3 {
  text-align: left;
  text-indent: 0;
  margin-top: 1.8em;
  margin-bottom: 0.6em;
  page-break-after: avoid;
  break-after: avoid;
}

h1 { page-break-before: auto; break-before: auto; }
h1.chapter-title { page-break-before: always; break-before: page; margin-top: 2em; }
h2, h3 { page-break-before: auto; break-before: auto; page-break-after: avoid; break-after: avoid; }
body > section:first-child .chapter-title:first-child { page-break-before: avoid; break-before: auto; }
h1 + p, h2 + p, h3 + p, figure + p { text-indent: 0; }

figure {
  margin: 1.5em 0;
  text-align: center;
  page-break-inside: avoid;
  break-inside: avoid;
}

figure img, figure svg, img, svg {
  max-width: 100%;
  height: auto;
  display: block;
  margin: 1.5em auto;
}

figcaption {
  font-size: 0.85em;
  margin-top: 0.5em;
  text-indent: 0;
  color: #555;
}

pre, code {
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
}

pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
"""


EPUB_CSS += """
strong { font-weight: bold; }
em { font-style: italic; }
sup, sub { line-height: 0; font-size: 0.75em; }
a.noteref, a[epub|type~="noteref"] { text-decoration: none; }
aside[epub|type~="footnote"] { font-size: 0.85em; margin: 0.6em 0; }
aside[epub|type~="footnote"] p { text-indent: 0; text-align: left; }
pre code { display: block; }
table { border-collapse: collapse; margin: 1.5em auto; max-width: 100%; }
th, td { border: 1px solid #999; padding: 0.25em 0.5em; text-indent: 0; text-align: left; vertical-align: top; }
th { background: #eee; }
section.titlepage { text-align: center; margin-top: 15%; }
section.titlepage h1, section.titlepage h2 { text-align: center; }
p.doc-author, p.doc-imprint { text-indent: 0; text-align: center; margin: 0.8em 0; }
p.doc-author { font-size: 1.2em; }
p.doc-imprint { font-size: 0.9em; color: #444; }
body.cover { margin: 0; text-align: center; }
body.cover svg { margin: 0 auto; max-height: 100vh; }
"""

EPOCH_DATE = "1980-01-01T00:00:00Z"
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
XHTML_NS = 'xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"'

INLINE_TAGS = {
    STRONG_OPEN: "<strong>",
    STRONG_CLOSE: "</strong>",
    EM_OPEN: "<em>",
    EM_CLOSE: "</em>",
    SUP_OPEN: "<sup>",
    SUP_CLOSE: "</sup>",
    SUB_OPEN: "<sub>",
    SUB_CLOSE: "</sub>",
}

LEGAL_ATTRIBUTIONS = (
    ("PyMuPDF / fitz", "AGPL-3.0 or commercial license; Artifex Software, Inc."),
    ("Pillow", "HPND License; Alex Clark and Pillow contributors."),
    ("EbookLib", "LGPL-3.0; Aleksandar Erkalović and contributors."),
    ("pyphen", "LGPL-2.1+, MPL-1.1, or GPL-2.0+; Guillaume Ayoub and contributors."),
    ("ftfy", "Apache License 2.0; Luminoso Technologies, Inc."),
    ("lxml", "BSD-3-Clause and ZPL-2.0; lxml project."),
    ("pypandoc", "MIT License; Juho Vepsäläinen and contributors."),
    ("python-docx", "MIT License; Steve Canny and contributors."),
)


@dataclass
class PackageItem:
    file_name: str
    media_type: str
    content: bytes
    item_id: str = ""
    properties: str = ""
    title: str = ""


@dataclass(frozen=True)
class BuildResult:
    output_path: Path
    counts: dict = field(default_factory=dict)
    chapter_files: int = 0


class EpubBuilder:
    def __init__(self, output_path: str | Path, title: str = DEFAULT_TITLE, language: str = DEFAULT_LANGUAGE) -> None:
        self.output_path = Path(output_path)
        self.title = title
        self.language = language
        self._note_files: dict[int, str] = {}
        self._counts = {"tables": 0, "images": 0, "figures": 0, "notes": 0, "note_refs": 0}

    def build(self, document: CleanedDocument) -> BuildResult:
        self._note_files = {}
        self._counts = {"tables": 0, "images": 0, "figures": 0, "notes": 0, "note_refs": 0}
        for chapter_index, chapter in enumerate(document.chapters, start=1):
            for item in chapter.items:
                if isinstance(item, FlowNoteUnit):
                    self._note_files[item.note_id] = f"chapter_{chapter_index}.xhtml"

        created = document.created or EPOCH_DATE
        zip_stamp = self._zip_timestamp(created)
        title_page = document.title_page
        author = ", ".join(title_page.authors) if title_page and title_page.authors else "Unknown"

        items: list[PackageItem] = []
        spine: list[PackageItem] = []
        items.append(PackageItem("styles/book.css", "text/css", EPUB_CSS.encode("utf-8"), "style"))

        cover_item: PackageItem | None = None
        cover = document.cover
        if cover is not None and cover.kind == COVER_IMAGE and cover.jpeg:
            cover_item = PackageItem("images/cover.jpg", "image/jpeg", cover.jpeg, "cover-image", "cover-image")
            items.append(cover_item)
            width, height = cover.width or 1600, cover.height or 2560
            cover_page = PackageItem(
                "text/cover.xhtml",
                "application/xhtml+xml",
                self._cover_xhtml(width, height).encode("utf-8"),
                "cover",
                "svg",
                "Cover",
            )
            items.append(cover_page)
            spine.append(cover_page)
        else:
            svg = synthesize_cover_svg(
                title_page.title if title_page and title_page.title else self.title,
                author if author != "Unknown" else "",
                title_page.subtitle if title_page else "",
            )
            cover_item = PackageItem("images/cover.svg", "image/svg+xml", svg, "cover-image", "cover-image")
            items.append(cover_item)
            cover_page = PackageItem(
                "text/cover.xhtml",
                "application/xhtml+xml",
                self._fallback_cover_xhtml().encode("utf-8"),
                "cover",
                "svg",
                "Cover",
            )
            items.append(cover_page)
            spine.append(cover_page)

        if title_page is not None:
            page = PackageItem(
                "text/titlepage.xhtml",
                "application/xhtml+xml",
                self._wrap_xhtml(
                    title_page.title or self.title,
                    self._title_page_html(title_page),
                    "../",
                ).encode("utf-8"),
                "titlepage",
                "",
                "Title Page",
            )
            items.append(page)
            spine.append(page)

        chapter_items: list[PackageItem] = []
        for chapter_index, chapter in enumerate(document.chapters, start=1):
            html_item, image_items = self._build_chapter(chapter_index, chapter)
            items.extend(image_items)
            items.append(html_item)
            chapter_items.append(html_item)
            spine.append(html_item)

        content_chapter_count = len(chapter_items)
        legal_item = self._build_legal_chapter()
        items.append(legal_item)
        chapter_items.append(legal_item)
        spine.append(legal_item)

        nav = PackageItem(
            "nav.xhtml",
            "application/xhtml+xml",
            self._nav_xhtml(chapter_items, spine, title_page is not None, True).encode("utf-8"),
            "nav",
            "nav",
            "Contents",
        )
        identifier = self._identifier(document, items)
        opf = self._opf(identifier, author, created, items, nav, spine)

        files: dict[str, tuple[bytes, bool]] = {
            "META-INF/container.xml": (CONTAINER_XML.encode("utf-8"), True),
            "EPUB/content.opf": (opf.encode("utf-8"), True),
            "EPUB/nav.xhtml": (nav.content, True),
        }
        for item in items:
            files[f"EPUB/{item.file_name}"] = (item.content, item.media_type.startswith("image/") is False)
        self._write_zip(files, zip_stamp)
        counts = dict(self._counts)
        return BuildResult(output_path=self.output_path, counts=counts, chapter_files=content_chapter_count)

    def _build_legal_chapter(self) -> PackageItem:
        attribution_items = "".join(
            f"<li><strong>{html.escape(name)}</strong>: {html.escape(terms)}</li>"
            for name, terms in LEGAL_ATTRIBUTIONS
        )
        body = (
            '<h1 class="chapter-title">About This Edition &amp; Licensing</h1>'
            '<p>This edition was converted using the EmperorOfBooks PDF-to-EPUB engine.</p>'
            '<section aria-labelledby="third-party-attributions"><h2 id="third-party-attributions">'
            f'Third-Party Attributions</h2><ul>{attribution_items}</ul></section>'
            '<p>Repository license and notices: '
            '<a href="https://github.com/EmperorOfBooks/PDF-to-EPUB">EmperorOfBooks/PDF-to-EPUB</a>.</p>'
        )
        return PackageItem(
            "text/colophon_legal.xhtml",
            "application/xhtml+xml",
            self._wrap_xhtml("About This Edition & Licensing", body, "../").encode("utf-8"),
            "colophon_legal",
            "",
            "About This Edition & Licensing",
        )

    @staticmethod
    def _item_id(item: PackageItem) -> str:
        return item.item_id or re.sub(r"\W", "_", item.file_name)

    def _write_zip(self, files: dict[str, tuple[bytes, bool]], stamp: tuple[int, int, int, int, int, int]) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(self.output_path, "w") as archive:
            info = zipfile.ZipInfo("mimetype", date_time=stamp)
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o644 << 16
            info.create_system = 3
            archive.writestr(info, b"application/epub+zip")
            for name in sorted(files):
                data, compress = files[name]
                entry = zipfile.ZipInfo(name, date_time=stamp)
                entry.compress_type = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
                entry.external_attr = 0o644 << 16
                entry.create_system = 3
                archive.writestr(entry, data)

    @staticmethod
    def _zip_timestamp(created: str) -> tuple[int, int, int, int, int, int]:
        match = re.match(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})", created or "")
        if not match:
            return ZIP_EPOCH
        year, month, day, hour, minute, second = (int(group) for group in match.groups())
        if year < 1980 or not (1 <= month <= 12 and 1 <= day <= 31):
            return ZIP_EPOCH
        return (year, month, day, hour, minute, second - second % 2)

    def _identifier(self, document: CleanedDocument, items: list[PackageItem]) -> str:
        digest = hashlib.sha256(self.title.encode("utf-8"))
        for item in sorted(items, key=lambda entry: entry.file_name):
            digest.update(item.file_name.encode("utf-8"))
            digest.update(item.content)
        return f"urn:uuid:{uuid.UUID(bytes=digest.digest()[:16], version=5)}"

    def _opf(
        self,
        identifier: str,
        author: str,
        created: str,
        items: list[PackageItem],
        nav: PackageItem,
        spine: list[PackageItem],
    ) -> str:
        manifest = []
        for item in [nav, *items]:
            item_id = self._item_id(item)
            props = f' properties="{item.properties}"' if item.properties and item.item_id != "style" else ""
            if item.item_id == "nav":
                props = ' properties="nav"'
            manifest.append(
                f'    <item id="{html.escape(item_id)}" href="{html.escape(item.file_name)}" media-type="{item.media_type}"{props}/>'
            )
        spine_refs = "\n".join(
            f'    <itemref idref="{html.escape(self._item_id(item))}"/>' for item in spine
        )
        return (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id" '
            f'xml:lang="{html.escape(self.language)}" prefix="schema: http://schema.org/">\n'
            '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
            f'    <dc:identifier id="book-id">{identifier}</dc:identifier>\n'
            f"    <dc:title>{html.escape(self.title)}</dc:title>\n"
            f"    <dc:language>{html.escape(self.language)}</dc:language>\n"
            f"    <dc:creator>{html.escape(author)}</dc:creator>\n"
            "    <dc:publisher>PDF to EPUB</dc:publisher>\n"
            f'    <meta property="dcterms:modified">{created}</meta>\n'
            '    <meta property="schema:accessMode">textual</meta>\n'
            '    <meta property="schema:accessMode">visual</meta>\n'
            '    <meta property="schema:accessModeSufficient">textual</meta>\n'
            '    <meta property="schema:accessibilityFeature">structuralNavigation</meta>\n'
            '    <meta property="schema:accessibilityFeature">tableOfContents</meta>\n'
            '    <meta property="schema:accessibilityHazard">none</meta>\n'
            '    <meta property="schema:accessibilitySummary">Reflowable text with a table of contents, '
            "structural navigation and ARIA document landmarks.</meta>\n"
            "  </metadata>\n"
            "  <manifest>\n" + "\n".join(manifest) + "\n  </manifest>\n"
            "  <spine>\n" + spine_refs + "\n  </spine>\n"
            "</package>\n"
        )

    def _nav_xhtml(self, chapters: list[PackageItem], spine: list[PackageItem], has_title: bool, has_cover: bool) -> str:
        toc = "\n".join(
            f'      <li><a href="{html.escape(item.file_name)}">{html.escape(item.title or "Untitled")}</a></li>' for item in chapters
        )
        landmarks = []
        if has_cover:
            landmarks.append('      <li><a epub:type="cover" href="text/cover.xhtml">Cover</a></li>')
        if has_title:
            landmarks.append('      <li><a epub:type="titlepage" href="text/titlepage.xhtml">Title Page</a></li>')
        if chapters:
            landmarks.append(
                f'      <li><a epub:type="bodymatter" href="{html.escape(chapters[0].file_name)}">Start of Content</a></li>'
            )
        return (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            f"<!DOCTYPE html>\n<html {XHTML_NS} xml:lang=\"{html.escape(self.language)}\" lang=\"{html.escape(self.language)}\">\n"
            "<head><meta charset=\"utf-8\" /><title>Contents</title>"
            '<link rel="stylesheet" type="text/css" href="styles/book.css" /></head>\n<body>\n'
            '  <nav epub:type="toc" role="doc-toc" id="toc">\n    <h1>Contents</h1>\n    <ol>\n' + toc + "\n    </ol>\n  </nav>\n"
            '  <nav epub:type="landmarks" id="landmarks" hidden="hidden">\n    <h2>Landmarks</h2>\n    <ol>\n'
            + "\n".join(landmarks)
            + "\n    </ol>\n  </nav>\n</body>\n</html>\n"
        )

    def _cover_xhtml(self, width: int, height: int) -> str:
        return (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            f"<!DOCTYPE html>\n<html {XHTML_NS} xml:lang=\"{html.escape(self.language)}\" lang=\"{html.escape(self.language)}\">\n"
            "<head><meta charset=\"utf-8\" /><title>Cover</title>"
            '<link rel="stylesheet" type="text/css" href="../styles/book.css" /></head>\n'
            '<body class="cover"><section epub:type="cover" role="region" aria-label="Cover">'
            f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'version="1.1" viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Cover">'
            f'<image width="{width}" height="{height}" xlink:href="../images/cover.jpg" /></svg>'
            "</section></body></html>\n"
        )

    def _fallback_cover_xhtml(self) -> str:
        return (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            f"<!DOCTYPE html>\n<html {XHTML_NS} xml:lang=\"{html.escape(self.language)}\" lang=\"{html.escape(self.language)}\">\n"
            "<head><meta charset=\"utf-8\" /><title>Cover</title>"
            '<link rel="stylesheet" type="text/css" href="../styles/book.css" /></head>\n'
            '<body class="cover"><section epub:type="cover" role="region" aria-label="Cover">'
            '<img src="../images/cover.svg" alt="Book cover" />'
            "</section></body></html>\n"
        )

    def _title_page_html(self, title_page: TitlePage) -> str:
        parts = ['<section epub:type="titlepage" role="region" aria-label="Title page" class="titlepage">']
        if title_page.title:
            parts.append(f"<h1>{html.escape(title_page.title)}</h1>")
        if title_page.subtitle:
            parts.append(f"<h2>{html.escape(title_page.subtitle)}</h2>")
        for author in title_page.authors:
            parts.append(f'<p class="doc-author">{html.escape(author)}</p>')
        for imprint in (*title_page.imprints, *title_page.extras):
            parts.append(f'<p class="doc-imprint">{html.escape(imprint)}</p>')
        parts.append("</section>")
        return "".join(parts)

    def _render_inline(self, text: str, seen_refs: set[int] | None = None, current_file: str = "") -> str:
        escaped = html.escape(text, quote=False)
        seen = seen_refs if seen_refs is not None else set()

        def note(match: re.Match[str]) -> str:
            note_id = int(match.group(1))
            label = match.group(2)
            if note_id in seen or note_id not in self._note_files:
                return f"<sup>{label}</sup>"
            seen.add(note_id)
            self._counts["note_refs"] += 1
            target_file = self._note_files[note_id]
            prefix = "" if target_file == current_file else target_file
            return (
                f'<a href="{prefix}#fn{note_id}" id="fnref{note_id}" epub:type="noteref">{label}</a>'
            )

        escaped = NOTE_RE.sub(note, escaped)
        for sentinel, tag in INLINE_TAGS.items():
            escaped = escaped.replace(sentinel, tag)
        return SENTINEL_RE.sub("", escaped)

    def _build_chapter(
        self, chapter_index: int, chapter: ChapterContent
    ) -> tuple[PackageItem, list[PackageItem]]:
        html_parts: list[str] = []
        note_parts: list[str] = []
        image_items: list[PackageItem] = []
        image_index = 0
        seen_images: set[str] = set()
        seen_refs: set[int] = set()
        file_name = f"chapter_{chapter_index}.xhtml"
        for item in chapter.items:
            if isinstance(item, FlowNoteUnit):
                self._note_files.setdefault(item.note_id, file_name)

        item_index = 0
        while item_index < len(chapter.items):
            item = chapter.items[item_index]
            if isinstance(item, FlowNoteUnit):
                body = self._render_inline(item.text, seen_refs, file_name)
                note_parts.append(
                    f'<aside id="fn{item.note_id}" epub:type="footnote" role="note">'
                    f'<p><a href="#fnref{item.note_id}">{html.escape(item.marker.strip("[]"))}.</a> {body}</p></aside>'
                )
                self._counts["notes"] += 1
                item_index += 1
                continue
            if isinstance(item, FlowTableUnit):
                html_parts.append(self._render_table(item))
                self._counts["tables"] += 1
                item_index += 1
                continue
            if isinstance(item, FlowTextUnit):
                if item.kind == "scene-break":
                    html_parts.append(f'<p class="scene-break">{html.escape(item.text)}</p>')
                elif item.kind == "heading":
                    heading_text = item.text.strip()
                    next_item = chapter.items[item_index + 1] if item_index + 1 < len(chapter.items) else None
                    if isinstance(next_item, FlowTextUnit) and next_item.kind == "heading":
                        subtitle = next_item.text.strip()
                        html_parts.append(
                            f'<h1 class="chapter-title">{html.escape(heading_text)} '
                            f'<span class="subtitle">{html.escape(subtitle)}</span></h1>'
                        )
                        item_index += 2
                        continue
                    tag = "h1" if item.is_chapter_heading else "h2"
                    class_attr = ' class="chapter-title"' if tag == "h1" else ""
                    html_parts.append(f"<{tag}{class_attr}>{html.escape(heading_text)}</{tag}>")
                elif item.kind == "code":
                    html_parts.append(f"<pre><code>{html.escape(item.text, quote=False)}</code></pre>")
                else:
                    html_parts.append(f"<p>{self._render_inline(item.text, seen_refs, file_name)}</p>")
                item_index += 1
                continue

            image_key = hashlib.sha256(item.image_bytes).hexdigest()
            if image_key in seen_images:
                item_index += 1
                continue
            seen_images.add(image_key)
            image_index += 1
            extension = self._normalize_extension(item.extension)
            image_file_name = f"images/chapter_{chapter_index}_{image_index}.{extension}"
            image_items.append(
                PackageItem(
                    image_file_name,
                    self._media_type_for_extension(extension),
                    item.image_bytes,
                    f"image_{chapter_index}_{image_index}",
                )
            )
            self._counts["figures" if item.is_vector else "images"] += 1
            alt_text = html.escape(item.alt or item.caption or "Figure illustration", quote=True)
            caption = f"<figcaption>{html.escape(item.caption)}</figcaption>" if item.caption else ""
            figure_class = "figure-block" if item.full_width else "figure-inline"
            html_parts.append(
                f'<figure class="{figure_class}"><img src="../{html.escape(image_file_name)}" alt="{alt_text}" />{caption}</figure>'
            )
            item_index += 1

        if not html_parts and not note_parts:
            html_parts.append("<p></p>")
        content = self._wrap_xhtml(chapter.title, "".join(html_parts + note_parts), "../")
        html_item = PackageItem(
            f"text/{file_name}",
            "application/xhtml+xml",
            content.encode("utf-8"),
            f"chapter_{chapter_index}",
            "",
            chapter.title,
        )
        return html_item, image_items

    def _render_table(self, table: FlowTableUnit) -> str:
        rows = [row for row in table.rows if any(cell.strip() for cell in row)]
        if not rows:
            return ""
        width = max(len(row) for row in rows)
        padded = [tuple(row) + ("",) * (width - len(row)) for row in rows]

        def cells(row: tuple[str, ...], tag: str) -> str:
            scope = ' scope="col"' if tag == "th" else ""
            return "".join(f"<{tag}{scope}>{html.escape(' '.join(cell.split()))}</{tag}>" for cell in row)

        head = f"<thead><tr>{cells(padded[0], 'th')}</tr></thead>"
        body_rows = "".join(f"<tr>{cells(row, 'td')}</tr>" for row in padded[1:])
        body = f"<tbody>{body_rows}</tbody>" if body_rows else ""
        return f"<table>{head}{body}</table>"

    def _wrap_xhtml(self, title: str, body: str, asset_prefix: str = "../") -> str:
        section = body
        if not body.startswith("<section"):
            section = f'<section epub:type="chapter" role="doc-chapter" id="chapter">{body}</section>'
        return (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            f'<!DOCTYPE html>\n<html {XHTML_NS} xml:lang="{html.escape(self.language)}" lang="{html.escape(self.language)}">'
            '<head><meta charset="utf-8" /><title>'
            + html.escape(title or "Untitled")
            + f'</title><link rel="stylesheet" type="text/css" href="{asset_prefix}styles/book.css" /></head><body>'
            + section
            + "</body></html>\n"
        )

    def _media_type_for_extension(self, extension: str) -> str:
        return {
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
            "gif": "image/gif",
            "webp": "image/webp",
            "svg": "image/svg+xml",
            "bmp": "image/bmp",
        }.get(extension, "application/octet-stream")

    def _normalize_extension(self, extension: str) -> str:
        normalized = extension.lower().strip().lstrip(".")
        return normalized or "png"


CONTAINER_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
    '  <rootfiles>\n    <rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/>\n'
    "  </rootfiles>\n</container>\n"
)

Builder = EpubBuilder
