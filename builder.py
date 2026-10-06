from __future__ import annotations

import hashlib
import html
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ebooklib import epub

from cleaner import ChapterContent, CleanedDocument, FlowImageUnit, FlowTableUnit, FlowTextUnit
from config import DEFAULT_LANGUAGE, DEFAULT_TITLE


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

table {
  border-collapse: collapse;
  width: 100%;
  margin: 1.2em 0;
}

table, th, td {
  border: 1px solid #999;
}

th, td {
  padding: 0.4em 0.6em;
  text-align: left;
  vertical-align: top;
}

th {
  background: #f0f0f0;
  font-weight: bold;
}
"""


@dataclass(frozen=True)
class BuildResult:
    output_path: Path


class EpubBuilder:
    def __init__(self, output_path: str | Path, title: str = DEFAULT_TITLE, language: str = DEFAULT_LANGUAGE) -> None:
        self.output_path = Path(output_path)
        self.title = title
        self.language = language

    def build(self, document: CleanedDocument, include_colophon: bool = False) -> BuildResult:
        book = epub.EpubBook()
        book.set_identifier(self._identifier(document))
        book.set_title(self.title)
        book.set_language(self.language)
        book.add_author("Unknown")
        book.add_metadata("DC", "publisher", "PDF to EPUB")
        modified = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        book.add_metadata("OPF", "meta", modified, {"property": "dcterms:modified"})
        stylesheet = epub.EpubItem(
            uid="book_style",
            file_name="styles/book.css",
            media_type="text/css",
            content=EPUB_CSS.encode("utf-8"),
        )
        book.add_item(stylesheet)

        epub_chapters: list[epub.EpubHtml] = []
        toc_entries: list[epub.Link] = []
        for chapter_index, chapter in enumerate(document.chapters, start=1):
            html_item, image_items = self._build_chapter(chapter_index, chapter)
            html_item.add_link(href="../styles/book.css", rel="stylesheet", type="text/css")
            for image_item in image_items:
                book.add_item(image_item)
            book.add_item(html_item)
            epub_chapters.append(html_item)
            toc_entries.append(epub.Link(html_item.file_name, chapter.title, f"chapter_{chapter_index}"))

        if include_colophon:
            colophon_item = self._build_colophon(modified)
            colophon_item.add_link(href="../styles/book.css", rel="stylesheet", type="text/css")
            book.add_item(colophon_item)
            epub_chapters.append(colophon_item)
            toc_entries.append(epub.Link(colophon_item.file_name, "Colophon", "colophon"))

        book.toc = tuple(toc_entries)
        book.spine = ["nav", *epub_chapters]
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        epub.write_epub(str(self.output_path), book)
        return BuildResult(output_path=self.output_path)

    def _build_chapter(self, chapter_index: int, chapter: ChapterContent) -> tuple[epub.EpubHtml, list[epub.EpubItem]]:
        html_parts: list[str] = []
        image_items: list[epub.EpubItem] = []
        image_index = 0
        previous_heading: str | None = None
        seen_images: set[str] = set()

        item_index = 0
        while item_index < len(chapter.items):
            item = chapter.items[item_index]
            if isinstance(item, FlowTextUnit):
                if item.kind == "heading":
                    heading_text = item.text.strip()
                    next_item = chapter.items[item_index + 1] if item_index + 1 < len(chapter.items) else None
                    if isinstance(next_item, FlowTextUnit) and next_item.kind == "heading":
                        subtitle = next_item.text.strip()
                        html_parts.append(
                            f'<h1 class="chapter-title">{html.escape(heading_text)} '
                            f'<span class="subtitle">{html.escape(subtitle)}</span></h1>'
                        )
                        previous_heading = subtitle
                        item_index += 2
                        continue
                    tag = "h1" if item.is_chapter_heading else "h2"
                    class_attr = ' class="chapter-title"' if tag == "h1" else ""
                    html_parts.append(f"<{tag}{class_attr}>{html.escape(heading_text)}</{tag}>")
                    previous_heading = heading_text
                else:
                    html_parts.append(f"<p>{html.escape(item.text)}</p>")
                item_index += 1
                continue

            if isinstance(item, FlowTableUnit):
                html_parts.append(self._render_table(item))
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
            image_item = epub.EpubItem(
                uid=f"image_{chapter_index}_{image_index}",
                file_name=image_file_name,
                media_type=self._media_type_for_extension(extension),
                content=item.image_bytes,
            )
            image_items.append(image_item)
            alt_text = html.escape(item.alt or item.caption or "Figure illustration", quote=True)
            caption = f"<figcaption>{html.escape(item.caption)}</figcaption>" if item.caption else ""
            figure_class = "figure-block" if item.full_width else "figure-inline"
            html_parts.append(
                f'<figure class="{figure_class}"><img src="../{html.escape(image_file_name)}" alt="{alt_text}" />{caption}</figure>'
            )
            item_index += 1

        if not html_parts:
            html_parts.append("<p></p>")
        content = self._wrap_xhtml(chapter.title, "".join(html_parts))
        html_item = epub.EpubHtml(
            title=chapter.title,
            file_name=f"text/chapter_{chapter_index}.xhtml",
            lang=self.language,
            uid=f"chapter_{chapter_index}",
        )
        html_item.set_content(content.encode("utf-8"))
        return html_item, image_items

    def _render_table(self, table: FlowTableUnit) -> str:
        rows_html: list[str] = []
        for row_index, row in enumerate(table.rows):
            use_header = table.has_header_row and row_index == 0
            cell_tag = "th" if use_header else "td"
            cells_html = "".join(
                f"<{cell_tag}>{html.escape(cell).replace(chr(10), '<br/>')}</{cell_tag}>" for cell in row
            )
            rows_html.append(f"<tr>{cells_html}</tr>")
        return f"<table>{''.join(rows_html)}</table>"

    def _build_colophon(self, generated_at: str) -> epub.EpubHtml:
        """Optional closing page (only when --include-colophon is passed) documenting
        how this EPUB was generated, decoupled from the default conversion output."""
        body = (
            f"<h1>Colophon</h1>"
            f"<p>This EPUB edition of <em>{html.escape(self.title)}</em> was generated automatically "
            f"from the source document using the EmperorOfBooks PDF-to-EPUB conversion engine.</p>"
            f"<p>Generated: {html.escape(generated_at)}</p>"
        )
        content = self._wrap_xhtml("Colophon", body)
        html_item = epub.EpubHtml(
            title="Colophon",
            file_name="text/colophon.xhtml",
            lang=self.language,
            uid="colophon",
        )
        html_item.set_content(content.encode("utf-8"))
        return html_item

    def _wrap_xhtml(self, title: str, body: str) -> str:
        return (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
            "<head><meta charset=\"utf-8\" /><title>"
            + html.escape(title)
            + "</title></head><body><section epub:type=\"chapter\" id=\"chapter\">"
            + body
            + "</section></body></html>"
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

    def _is_title_variant(self, left: str, right: str) -> bool:
        left_norm = self._title_key(left)
        right_norm = self._title_key(right)
        if not left_norm or not right_norm:
            return False
        if left_norm == right_norm or left_norm in right_norm or right_norm in left_norm:
            return True
        left_words = set(left_norm.split())
        right_words = set(right_norm.split())
        return len(left_words & right_words) >= max(2, min(len(left_words), len(right_words)) - 1)

    def _title_key(self, value: str) -> str:
        text = html.unescape(value).strip().lower().replace("&", " and ")
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", text)).strip()

    def _identifier(self, document: CleanedDocument) -> str:
        """Derive a deterministic UUID5 from the book's title and content, so re-converting
        the same source produces a stable EPUB identifier instead of a new random one."""
        hasher = hashlib.sha256()
        hasher.update(self.title.encode("utf-8"))
        for chapter in document.chapters:
            hasher.update(chapter.title.encode("utf-8"))
            for item in chapter.items:
                if isinstance(item, FlowTextUnit):
                    hasher.update(item.text.encode("utf-8"))
        content_hash = hasher.hexdigest()
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"urn:emperorofbooks:pdf-to-epub:{content_hash}"))


Builder = EpubBuilder
