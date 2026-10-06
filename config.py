from __future__ import annotations

import re

HEADER_FOOTER_MARGIN_RATIO = 0.05
MARGIN_ARTIFACT_TOP_RATIO = 0.08
MARGIN_ARTIFACT_BOTTOM_RATIO = 0.92
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
    # German
    re.compile(r"^kapitel\s+([0-9ivxlcdm]+|eins|zwei|drei|vier|fünf|sechs|sieben|acht|neun|zehn)\b", re.IGNORECASE),
    re.compile(r"^(prolog|epilog|vorwort|einleitung|anhang)\b", re.IGNORECASE),
    # French
    re.compile(r"^chapitre\s+([0-9ivxlcdm]+|un|deux|trois|quatre|cinq|six|sept|huit|neuf|dix)\b", re.IGNORECASE),
    re.compile(r"^(prologue|épilogue|préface|introduction|annexe)\b", re.IGNORECASE),
    # Spanish
    re.compile(r"^cap[ií]tulo\s+([0-9ivxlcdm]+|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\b", re.IGNORECASE),
    re.compile(r"^(pr[óo]logo|ep[íi]logo|prefacio|introducci[óo]n|ap[ée]ndice)\b", re.IGNORECASE),
    # Italian
    re.compile(r"^capitolo\s+([0-9ivxlcdm]+|uno|due|tre|quattro|cinque|sei|sette|otto|nove|dieci)\b", re.IGNORECASE),
    # Russian (Cyrillic)
    re.compile(r"^глава\s+[0-9ivxlcdm]+", re.IGNORECASE | re.UNICODE),
    re.compile(r"^(пролог|эпилог|предисловие|введение|приложение)\b", re.IGNORECASE | re.UNICODE),
    # CJK: "第...章" (Chapter N) and "第...节"/"第...部" variants
    re.compile(r"^第[0-9一二三四五六七八九十百千0-9]+[章节部回]"),
    re.compile(r"^(序章|序言|前言|引言|附录|尾声)"),
)
DEFAULT_LANGUAGE = "en"
DEFAULT_TITLE = "Converted Book"
PARAGRAPH_X_TOLERANCE = 24.0
