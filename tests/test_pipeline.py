import unittest
import os
import tempfile
import zipfile

from cleaner import ChapterContent, FlowImageUnit, FlowTextUnit, PdfCleaner
from epub_builder import EPUB_CSS, EpubBuilder
from extractor import ExtractedDocument, ImageData, LineData, PageData, PdfExtractor, TextBlockData
from layout import LayoutAnalyzer


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
        self.assertIn('<figure class="figure-inline">', content)
        self.assertIn('Diagram', content)
        self.assertIn('max-width: 100%', EPUB_CSS)
        self.assertIn('text-indent: 1.5em', EPUB_CSS)

    def test_consecutive_headings_render_as_one_chapter_title(self):
        chapter = ChapterContent(
            title='Chapter 1',
            items=(
                FlowTextUnit(kind='heading', text='Chapter 1', y=10.0, page_number=1, font_size=18.0, bold_ratio=0.8),
                FlowTextUnit(kind='heading', text='The Calling', y=30.0, page_number=1, font_size=16.0, bold_ratio=0.8),
            ),
        )

        html_item, _ = EpubBuilder('out.epub')._build_chapter(1, chapter)
        content = html_item.content.decode('utf-8')

        self.assertIn('<h1 class="chapter-title">Chapter 1 <span class="subtitle">The Calling</span></h1>', content)

    def test_micro_image_blocks_are_ignored(self):
        extractor = PdfExtractor('unused.pdf')

        self.assertIsNone(
            extractor._parse_image_block(
                1,
                0,
                {'image': b'not-used', 'width': 10, 'height': 10, 'ext': 'png'},
            )
        )

    def test_clean_heading_strips_trailing_page_markers(self):
        cleaner = PdfCleaner()

        self.assertEqual(cleaner._clean_heading_text('Chapter One pg. 12'), 'Chapter One')
        self.assertEqual(cleaner._clean_heading_text('Chapter Two -'), 'Chapter Two')

    def test_lowercase_block_continuation_is_joined_after_terminal_punctuation(self):
        first_block = TextBlockData(
            page_number=1,
            block_index=0,
            bbox=(40.0, 100.0, 500.0, 120.0),
            lines=(),
            text='The first block ends here.',
            max_font_size=12.0,
            avg_font_size=12.0,
            bold_ratio=0.0,
        )
        second_block = TextBlockData(
            page_number=1,
            block_index=1,
            bbox=(40.0, 125.0, 500.0, 145.0),
            lines=(),
            text='and this block continues the same paragraph.',
            max_font_size=12.0,
            avg_font_size=12.0,
            bold_ratio=0.0,
        )
        document = ExtractedDocument(
            pages=(PageData(page_number=1, width=600.0, height=800.0, text_blocks=(first_block, second_block), images=()),)
        )

        cleaned = PdfCleaner().clean(document)
        paragraphs = [
            item.text
            for chapter in cleaned.chapters
            for item in chapter.items
            if isinstance(item, FlowTextUnit) and item.kind == 'paragraph'
        ]

        self.assertEqual(paragraphs, ['The first block ends here. and this block continues the same paragraph.'])

    def test_lowercase_page_start_continuation_is_joined(self):
        first_page = PageData(
            page_number=1,
            width=600.0,
            height=800.0,
            text_blocks=(TextBlockData(1, 0, (40.0, 100.0, 500.0, 120.0), (), 'The paragraph continues', 12.0, 12.0, 0.0),),
            images=(),
        )
        second_page = PageData(
            page_number=2,
            width=600.0,
            height=800.0,
            text_blocks=(TextBlockData(2, 0, (40.0, 100.0, 500.0, 120.0), (), 'on the next page.', 12.0, 12.0, 0.0),),
            images=(),
        )

        cleaned = PdfCleaner().clean(ExtractedDocument(pages=(first_page, second_page)))
        paragraphs = [
            item.text
            for chapter in cleaned.chapters
            for item in chapter.items
            if isinstance(item, FlowTextUnit) and item.kind == 'paragraph'
        ]

        self.assertEqual(paragraphs, ['The paragraph continues on the next page.'])

    def test_uppercase_page_start_continuation_is_joined_when_previous_is_open(self):
        first_page = PageData(
            page_number=1,
            width=600.0,
            height=800.0,
            text_blocks=(TextBlockData(1, 0, (40.0, 100.0, 500.0, 120.0), (), 'The paragraph continues', 12.0, 12.0, 0.0),),
            images=(),
        )
        second_page = PageData(
            page_number=2,
            width=600.0,
            height=800.0,
            text_blocks=(TextBlockData(2, 0, (40.0, 100.0, 500.0, 120.0), (), 'Next page continues it.', 12.0, 12.0, 0.0),),
            images=(),
        )

        cleaned = PdfCleaner().clean(ExtractedDocument(pages=(first_page, second_page)))
        paragraphs = [item.text for chapter in cleaned.chapters for item in chapter.items if isinstance(item, FlowTextUnit) and item.kind == 'paragraph']

        self.assertEqual(paragraphs, ['The paragraph continues Next page continues it.'])

    def test_wide_block_forces_single_column(self):
        page = PageData(
            page_number=1,
            width=1000.0,
            height=1200.0,
            text_blocks=(TextBlockData(1, 0, (0.0, 100.0, 600.0, 140.0), (), 'Wide block', 12.0, 12.0, 0.0),),
            images=(),
        )

        self.assertEqual(LayoutAnalyzer().column_index(page, 800.0), 0)

    def test_single_line_body_sized_margin_block_is_artifact(self):
        block = TextBlockData(
            page_number=1,
            block_index=0,
            bbox=(40.0, 10.0, 200.0, 20.0),
            lines=(LineData('Running head', (40.0, 10.0, 200.0, 20.0), ()),),
            text='Running head',
            max_font_size=12.0,
            avg_font_size=12.0,
            bold_ratio=0.0,
        )
        page = PageData(1, 600.0, 800.0, (block,), ())

        self.assertTrue(LayoutAnalyzer().is_margin_artifact(page, block, 12.0))

    def test_subheading_does_not_create_new_chapter_file(self):
        blocks = (
            TextBlockData(1, 0, (40.0, 80.0, 300.0, 100.0), (), 'Chapter 1', 18.0, 18.0, 0.8),
            TextBlockData(1, 1, (40.0, 120.0, 300.0, 140.0), (), 'The Calling', 14.0, 14.0, 0.8),
            TextBlockData(1, 2, (40.0, 180.0, 500.0, 220.0), (), 'Body text follows.', 12.0, 12.0, 0.0),
        )
        cleaned = PdfCleaner().clean(ExtractedDocument(pages=(PageData(1, 600.0, 800.0, blocks, ()),)))

        self.assertEqual(len(cleaned.chapters), 1)
        self.assertIn('The Calling', cleaned.chapters[0].title)

    def test_colophon_is_omitted_by_default_and_included_when_requested(self):
        chapter = ChapterContent(
            title='Chapter 1',
            items=(FlowTextUnit(kind='heading', text='Chapter 1', y=10.0, page_number=1, font_size=18.0, bold_ratio=0.8),),
        )
        from cleaner import CleanedDocument

        document = CleanedDocument(chapters=(chapter,))

        with tempfile.TemporaryDirectory() as temp_dir:
            default_path = os.path.join(temp_dir, 'default.epub')
            colophon_path = os.path.join(temp_dir, 'colophon.epub')
            EpubBuilder(default_path).build(document)
            EpubBuilder(colophon_path).build(document, include_colophon=True)

            with zipfile.ZipFile(default_path) as archive:
                default_names = archive.namelist()
            with zipfile.ZipFile(colophon_path) as archive:
                colophon_names = archive.namelist()

            self.assertFalse(any('colophon' in name.lower() for name in default_names))
            self.assertTrue(any('colophon' in name.lower() for name in colophon_names))

    def test_ocr_heuristics_flag_filters_repeated_character_noise(self):
        cleaner_default = PdfCleaner()
        cleaner_ocr = PdfCleaner(ocr_heuristics=True)

        self.assertFalse(cleaner_default._is_noise_text('zzzzzz'))
        self.assertTrue(cleaner_ocr._is_noise_text('zzzzzz'))


if __name__ == '__main__':
    unittest.main()
