import unittest
import zipfile

from cleaner import ChapterContent, CleanedDocument, FlowImageUnit, FlowTextUnit, PdfCleaner
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

    def test_scene_break_renders_with_semantic_class(self):
        chapter = ChapterContent(
            title='Chapter 1',
            items=(FlowTextUnit(kind='scene-break', text='* * *', y=10.0, page_number=1, font_size=12.0, bold_ratio=0.0),),
        )

        html_item, _ = EpubBuilder('out.epub')._build_chapter(1, chapter)
        content = html_item.content.decode('utf-8')

        self.assertIn('<p class="scene-break">* * *</p>', content)

    def test_every_epub_contains_legal_colophon_in_spine_and_toc(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / 'legal.epub'
            EpubBuilder(output).build(
                CleanedDocument(chapters=(ChapterContent(title='Chapter 1', items=()),))
            )
            with zipfile.ZipFile(output) as archive:
                names = archive.namelist()
                nav = archive.read('EPUB/nav.xhtml').decode('utf-8')
                opf = archive.read('EPUB/content.opf').decode('utf-8')

        self.assertIn('EPUB/text/colophon_legal.xhtml', names)
        self.assertIn('About This Edition &amp; Licensing', nav)
        self.assertIn('colophon_legal', opf)

    def test_micro_image_blocks_are_ignored(self):
        extractor = PdfExtractor('unused.pdf')

        self.assertIsNone(
            extractor._parse_image_block(
                1,
                0,
                {'image': b'not-used', 'width': 10, 'height': 10, 'ext': 'png'},
            )
        )

    def test_clean_heading_normalizes_observed_chapter_fourteen_marker(self):
        cleaner = PdfCleaner()

        self.assertEqual(cleaner._clean_heading_text('Chapter 1&'), 'Chapter 14')
        self.assertEqual(cleaner._clean_heading_text('Chapter &'), 'Chapter 4')
        self.assertEqual(cleaner._clean_heading_text('Chapter S'), 'Chapter 5')

    def test_zero_width_pdf_separators_preserve_word_spacing(self):
        cleaner = PdfCleaner()

        self.assertEqual(
            cleaner._normalize_text('\u200bBy:\u200b\u200bKillian A.\u200b Bies\u200b'),
            'By: Killian A. Bies',
        )

    def test_zero_width_pdf_separators_are_removed_from_code_blocks(self):
        line = LineData(
            text='\u200bThe\u200b\u200bgroup\u200b',
            bbox=(40.0, 100.0, 120.0, 112.0),
            spans=(),
        )
        block = TextBlockData(
            page_number=1,
            block_index=0,
            bbox=(40.0, 100.0, 120.0, 112.0),
            lines=(line,),
            text=line.text,
            max_font_size=12.0,
            avg_font_size=12.0,
            bold_ratio=0.0,
        )
        page = PageData(1, 600.0, 800.0, (block,), ())

        code = PdfCleaner()._code_element(page, block, body_font_size=12.0)

        self.assertEqual(code.text, 'The group')

    def test_lowercase_block_after_terminal_punctuation_stays_separate(self):
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

        self.assertEqual(paragraphs, ['The first block ends here.', 'and this block continues the same paragraph.'])

    def test_quoted_sentence_is_not_merged_with_next_block(self):
        blocks = (
            TextBlockData(1, 0, (40.0, 100.0, 500.0, 120.0), (), '"Stop!"', 12.0, 12.0, 0.0),
            TextBlockData(1, 1, (40.0, 125.0, 500.0, 145.0), (), 'He turned away.', 12.0, 12.0, 0.0),
        )
        cleaned = PdfCleaner().clean(ExtractedDocument(pages=(PageData(1, 600.0, 800.0, blocks, ()),)))
        paragraphs = [item.text for chapter in cleaned.chapters for item in chapter.items if isinstance(item, FlowTextUnit) and item.kind == 'paragraph']

        self.assertEqual(paragraphs, ['"Stop!"', 'He turned away.'])

    def test_scene_break_is_preserved_as_semantic_flow_item(self):
        block = TextBlockData(1, 0, (40.0, 100.0, 500.0, 120.0), (), '* * *', 12.0, 12.0, 0.0)
        cleaned = PdfCleaner().clean(ExtractedDocument(pages=(PageData(1, 600.0, 800.0, (block,), ()),)))
        scene_breaks = [item for chapter in cleaned.chapters for item in chapter.items if isinstance(item, FlowTextUnit) and item.kind == 'scene-break']

        self.assertEqual([item.text for item in scene_breaks], ['* * *'])

    def test_oversized_off_page_watermark_is_dropped(self):
        body = TextBlockData(1, 0, (40.0, 100.0, 500.0, 120.0), (), 'Body text remains.', 12.0, 12.0, 0.0)
        watermark = TextBlockData(1, 1, (-20.0, 10.0, 380.0, 620.0), (), 'PROOF', 182.2, 182.2, 0.0)
        page = PageData(1, 396.0, 612.0, (body, watermark), ())

        items = PdfCleaner()._page_items(page, body_font_size=12.0, repeated_margin_texts=set())

        self.assertIn('Body text remains.', [item.text for item in items])
        self.assertNotIn('PROOF', [item.text for item in items])

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

    @staticmethod
    def _margin_page(text, y0=10.0, y1=20.0):
        block = TextBlockData(
            page_number=1,
            block_index=0,
            bbox=(40.0, y0, 200.0, y1),
            lines=(LineData(text, (40.0, y0, 200.0, y1), ()),),
            text=text,
            max_font_size=12.0,
            avg_font_size=12.0,
            bold_ratio=0.0,
        )
        return PageData(1, 600.0, 800.0, (block,), ()), block

    def test_numeric_margin_block_is_artifact(self):
        for text, y in (('12', 10.0), ('Page 3 of 9', 770.0)):
            page, block = self._margin_page(text, y, y + 10.0)
            self.assertTrue(LayoutAnalyzer().is_margin_artifact(page, block, 12.0), text)

    def test_glossary_label_near_page_edge_is_kept(self):
        page, block = self._margin_page('Onu-Arith:')

        self.assertFalse(LayoutAnalyzer().is_margin_artifact(page, block, 12.0))

    def test_justified_proportional_block_with_zero_width_spaces_is_not_code(self):
        from extractor import PdfExtractor
        text = 'A\u200b smaller\u200b mountain\u200b range\u200b lies\u200b to\u200b the\u200b west\u200b of\u200b it.'
        spans = [
            {'text': text, 'font': 'TimesNewRomanPSMT', 'flags': 4, 'size': 12.0,
             'origin': (40.0, 100.0), 'bbox': (40.0, 90.0, 40.0 + len(text) * 6.0, 104.0)}
            for _ in range(3)
        ]
        lines = [{'bbox': (40.0, 90.0 + i * 14, 500.0, 104.0 + i * 14), 'spans': [spans[i]]} for i in range(3)]
        block = {'bbox': (40.0, 90.0, 500.0, 140.0), 'lines': lines}
        parsed = PdfExtractor("unused.pdf")._parse_text_block(1, 0, block)

        self.assertFalse(parsed.is_code)

    def test_monospace_block_is_still_code(self):
        from extractor import PdfExtractor
        span = {'text': 'x = compute(1, 2)', 'font': 'Courier', 'flags': 8, 'size': 10.0,
                'origin': (40.0, 100.0), 'bbox': (40.0, 90.0, 150.0, 104.0)}
        block = {'bbox': (40.0, 90.0, 150.0, 104.0), 'lines': [{'bbox': span['bbox'], 'spans': [span]}]}

        self.assertTrue(PdfExtractor("unused.pdf")._parse_text_block(1, 0, block).is_code)
    def test_subheading_does_not_create_new_chapter_file(self):
        blocks = (
            TextBlockData(1, 0, (40.0, 80.0, 300.0, 100.0), (), 'Chapter 1', 18.0, 18.0, 0.8),
            TextBlockData(1, 1, (40.0, 120.0, 300.0, 140.0), (), 'The Calling', 14.0, 14.0, 0.8),
            TextBlockData(1, 2, (40.0, 180.0, 500.0, 220.0), (), 'Body text follows.', 12.0, 12.0, 0.0),
        )
        cleaned = PdfCleaner().clean(ExtractedDocument(pages=(PageData(1, 600.0, 800.0, blocks, ()),)))

        self.assertEqual(len(cleaned.chapters), 1)
        self.assertIn('The Calling', cleaned.chapters[0].title)


if __name__ == '__main__':
    unittest.main()
