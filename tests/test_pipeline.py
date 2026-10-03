import unittest

from cleaner import ChapterContent, FlowImageUnit, FlowTextUnit, PdfCleaner
from epub_builder import EpubBuilder
from extractor import ExtractedDocument, ImageData, PageData, TextBlockData


class PdfPipelineTests(unittest.TestCase):
    def test_multicolumn_layout_prefers_left_column_before_right_column(self):
        left_block = TextBlockData(
            page_number=1,
            block_index=0,
            bbox=(40.0, 300.0, 320.0, 420.0),
            lines=(),
            text='Left column body text',
            max_font_size=12.0,
            avg_font_size=12.0,
            bold_ratio=0.0,
        )
        right_block = TextBlockData(
            page_number=1,
            block_index=1,
            bbox=(520.0, 100.0, 820.0, 220.0),
            lines=(),
            text='Right column body text',
            max_font_size=12.0,
            avg_font_size=12.0,
            bold_ratio=0.0,
        )
        document = ExtractedDocument(
            pages=(
                PageData(
                    page_number=1,
                    width=1000.0,
                    height=1200.0,
                    text_blocks=(left_block, right_block),
                    images=(),
                ),
            )
        )

        cleaned = PdfCleaner().clean(document)
        paragraphs = [
            item.text
            for chapter in cleaned.chapters
            for item in chapter.items
            if isinstance(item, FlowTextUnit) and item.kind == "paragraph"
        ]

        self.assertEqual(paragraphs, ['Left column body text', 'Right column body text'])

    def test_epub_builder_uses_semantic_responsive_image_markup(self):
        chapter = ChapterContent(
            title='Chapter 1',
            items=(
                FlowTextUnit(kind='heading', text='Chapter 1', y=10.0, page_number=1, font_size=18.0, bold_ratio=0.75, level=1),
                FlowImageUnit(kind='image', y=80.0, page_number=1, image_bytes=b'fake-image-data', extension='png', alt='Diagram'),
                FlowTextUnit(kind='paragraph', text='Some body copy here.', y=160.0, page_number=1, font_size=12.0, bold_ratio=0.0, level=0),
            ),
        )

        html_item, _ = EpubBuilder('out.epub')._build_chapter(1, chapter)
        content = html_item.content.decode('utf-8')

        self.assertIn('<section', content)
        self.assertIn('aspect-ratio', content)
        self.assertIn('max-width:100%', content)
        self.assertIn('Diagram', content)


if __name__ == '__main__':
    unittest.main()
