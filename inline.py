"""Inline typography helpers.

Inline styling travels through the text pipeline as private-use sentinel
characters so that the cleaner's string-based paragraph assembly keeps working.
The builder converts the sentinels into real XHTML elements.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

STRONG_OPEN, STRONG_CLOSE = "\ue001", "\ue002"
EM_OPEN, EM_CLOSE = "\ue003", "\ue004"
SUP_OPEN, SUP_CLOSE = "\ue005", "\ue006"
SUB_OPEN, SUB_CLOSE = "\ue007", "\ue008"
NOTE_OPEN, NOTE_MID, NOTE_CLOSE = "\ue009", "\ue00a", "\ue00b"

SENTINEL_RE = re.compile("[\ue001-\ue00b]")
NOTE_RE = re.compile(f"{NOTE_OPEN}(\\d+){NOTE_MID}(.*?){NOTE_CLOSE}", re.DOTALL)
SUP_TOKEN_RE = re.compile(f"{SUP_OPEN}([^{SUP_CLOSE}]*){SUP_CLOSE}")
NOTE_MARKER_RE = re.compile(r"^(?:\[\d{1,3}\]|\d{1,3}|[*†‡§¶]{1,3})$")
LEADING_MARKER_RE = re.compile(r"^(\[\d{1,3}\]|\d{1,3}(?!\d)|[*†‡§¶]{1,3})[\.\):]?\s*(?=\S)")
ZERO_WIDTH_SPACE_RE = re.compile("\u200b+")
ZERO_WIDTH_NONBREAK_RE = re.compile("[\u2060\ufeff\ufffe\uffff]")

MONO_NAME_HINTS = ("mono", "courier", "consolas", "menlo", "typewriter", "lucida console", "inconsolata", "source code")
BOLD_NAME_HINTS = ("bold", "black", "heavy", "semibold", "demi")
ITALIC_NAME_HINTS = ("italic", "oblique", "slanted")
NARROW_CHARS = set("il1|.,;:!'tfjI")
WIDE_CHARS = set("mwMW@%")


def strip_markup(text: str) -> str:
    """Return visible text with all inline sentinels removed (note refs keep their label)."""
    text = NOTE_RE.sub(lambda match: match.group(2), text)
    return SENTINEL_RE.sub("", text)


def normalize_invisible_separators(text: str) -> str:
    """Turn extracted zero-width word separators into spaces and remove format marks."""
    text = ZERO_WIDTH_SPACE_RE.sub(" ", text)
    return ZERO_WIDTH_NONBREAK_RE.sub("", text)


def normalize_marker(marker: str) -> str:
    return marker.strip().strip("[]")


def is_bold_span(font: str, flags: int) -> bool:
    lowered = font.lower()
    return any(hint in lowered for hint in BOLD_NAME_HINTS) or bool(flags & 16)


def is_italic_span(font: str, flags: int) -> bool:
    lowered = font.lower()
    return any(hint in lowered for hint in ITALIC_NAME_HINTS) or bool(flags & 2)


def is_mono_font(font: str, flags: int) -> bool:
    lowered = font.lower()
    return any(hint in lowered for hint in MONO_NAME_HINTS) or bool(flags & 8)


@dataclass(frozen=True)
class StyledRun:
    text: str
    bold: bool
    italic: bool
    position: str  # "base", "sup" or "sub"


def span_position(size: float, origin_y: float | None, base_size: float, base_y: float | None, flags: int) -> str:
    """Classify a span as base text, superscript, or subscript from baseline offset."""
    if base_size <= 0:
        return "base"
    smaller = size < base_size * 0.88
    if origin_y is not None and base_y is not None:
        shift = origin_y - base_y
        if smaller and shift < -base_size * 0.18:
            return "sup"
        if smaller and shift > base_size * 0.12:
            return "sub"
        if not smaller:
            return "base"
    if flags & 1 and smaller:
        return "sup"
    return "base"


def render_runs(runs: Sequence[StyledRun]) -> str:
    merged: list[StyledRun] = []
    for run in runs:
        if merged and (merged[-1].bold, merged[-1].italic, merged[-1].position) == (run.bold, run.italic, run.position):
            merged[-1] = StyledRun(merged[-1].text + run.text, run.bold, run.italic, run.position)
        else:
            merged.append(run)
    parts: list[str] = []
    for run in merged:
        core = run.text.strip()
        if not core:
            parts.append(run.text)
            continue
        lead = run.text[: len(run.text) - len(run.text.lstrip())]
        trail = run.text[len(run.text.rstrip()):]
        wrapped = core
        if run.position == "sup":
            wrapped = f"{SUP_OPEN}{wrapped}{SUP_CLOSE}"
        elif run.position == "sub":
            wrapped = f"{SUB_OPEN}{wrapped}{SUB_CLOSE}"
        if run.italic:
            wrapped = f"{EM_OPEN}{wrapped}{EM_CLOSE}"
        if run.bold:
            wrapped = f"{STRONG_OPEN}{wrapped}{STRONG_CLOSE}"
        parts.append(f"{lead}{wrapped}{trail}")
    return "".join(parts)


def sup_tokens(markup: str) -> list[str]:
    return [normalize_marker(token) for token in SUP_TOKEN_RE.findall(markup) if NOTE_MARKER_RE.match(token.strip())]


def link_note_refs(markup: str, mapping: dict[str, int], used_keys: set[str] | None = None) -> str:
    """Replace the first superscript for each paired marker with a note reference sentinel."""
    used = used_keys if used_keys is not None else set()

    def substitute(match: re.Match[str]) -> str:
        token = match.group(1).strip()
        key = normalize_marker(token)
        if key in mapping and key not in used and NOTE_MARKER_RE.match(token):
            used.add(key)
            return f"{NOTE_OPEN}{mapping[key]}{NOTE_MID}{token}{NOTE_CLOSE}"
        return match.group(0)

    return SUP_TOKEN_RE.sub(substitute, markup)


def is_uniform_pitch(spans: Sequence[tuple[str, float]]) -> bool:
    """True when every (text, width) pair has the same per-character advance."""
    samples = [(text, width) for text, width in spans if len(text) >= 6 and width > 0]
    if len(samples) < 3:
        return False
    pitches = [width / len(text) for text, width in samples]
    mean = sum(pitches) / len(pitches)
    if mean <= 0 or any(abs(pitch - mean) / mean > 0.04 for pitch in pitches):
        return False
    joined = "".join(text for text, _ in samples)
    return any(ch in NARROW_CHARS for ch in joined) and any(ch in WIDE_CHARS or ch.isupper() for ch in joined)
