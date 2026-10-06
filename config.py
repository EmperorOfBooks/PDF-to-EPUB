from __future__ import annotations

import re

HEADER_FOOTER_MARGIN_RATIO = 0.05
HEADER_REPEAT_RATIO = 0.3
MIN_HEADER_REPEAT_COUNT = 2
FONT_SIZE_HEADLINE_DELTA = 1.4
FONT_SIZE_HEADLINE_MULTIPLIER = 1.2
BOLD_RATIO_THRESHOLD = 0.5
TERMINAL_PUNCTUATION = (".", "!", "?")
PAGE_NUMBER_PATTERN = re.compile(r"^\s*(?:\d+|[ivxlcdm]+)\s*$", re.IGNORECASE)
CHAPTER_PATTERNS = (
    re.compile(r"^chapter\s+([0-9ivxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten)\b", re.IGNORECASE),
    re.compile(r"^part\s+([0-9ivxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten)\b", re.IGNORECASE),
    re.compile(r"^(prologue|epilogue|appendix|preface|foreword|introduction)\b", re.IGNORECASE),
)
DEFAULT_LANGUAGE = "en"
DEFAULT_TITLE = "Converted Book"
PARAGRAPH_X_TOLERANCE = 24.0
XY_CUT_MIN_GUTTER = 6.0
XY_CUT_MIN_BAND_GAP = 0.5
OCR_MIN_CHARS = 50
OCR_BAD_GLYPH_RATIO = 0.30
OCR_MIN_PAGE_AREA = 40000.0
