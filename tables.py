"""Table structure reconstruction from ruling lines and whitespace grid alignment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import pypdfium2 as pdfium

BBox = tuple[float, float, float, float]

MIN_ROWS = 2
MIN_COLS = 2
MIN_FILLED_RATIO = 0.5
WS_MIN_ROWS = 3
WS_MIN_COLS = 3
WS_MAX_WORDS_PER_CELL = 6.0
WS_ROW_TOLERANCE = 3.0
WS_ANCHOR_TOLERANCE = 5.0


@dataclass(frozen=True)
class DetectedTable:
    bbox: BBox
    rows: tuple[tuple[str, ...], ...]
    method: str


def _clean_cell(value) -> str:
    return " ".join(str(value or "").split())


def detect_ruled_tables(page, words: Sequence[tuple] = ()) -> list[DetectedTable]:
    """Find simple ruled grids from PDFium path bounds and assign extracted words to cells."""
    try:
        _width, page_height = page.get_size()
        paths = page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_PATH])
    except Exception:
        return []
    horizontal: list[tuple[float, float, float]] = []
    vertical: list[tuple[float, float, float]] = []
    for path in paths:
        try:
            x0, y0, x1, y1 = path.get_bounds()
        except Exception:
            continue
        bbox = (min(x0, x1), page_height - max(y0, y1), max(x0, x1), page_height - min(y0, y1))
        left, top, right, bottom = bbox
        if right - left > 30 and bottom - top <= 3:
            horizontal.append((left, right, (top + bottom) / 2))
        elif bottom - top > 30 and right - left <= 3:
            vertical.append((top, bottom, (left + right) / 2))

    def coordinates(lines: list[tuple[float, float, float]], position: int) -> list[float]:
        values = sorted(line[position] for line in lines)
        groups: list[list[float]] = []
        for value in values:
            if groups and value - sum(groups[-1]) / len(groups[-1]) <= 3:
                groups[-1].append(value)
            else:
                groups.append([value])
        return [sum(group) / len(group) for group in groups]

    ys, xs = coordinates(horizontal, 2), coordinates(vertical, 2)
    if len(ys) < MIN_ROWS + 1 or len(xs) < MIN_COLS + 1:
        return []

    def crosses_h(y: float, x: float) -> bool:
        return any(abs(line_y - y) <= 3 and x0 - 3 <= x <= x1 + 3 for x0, x1, line_y in horizontal)

    def crosses_v(x: float, y: float) -> bool:
        return any(abs(line_x - x) <= 3 and top - 3 <= y <= bottom + 3 for top, bottom, line_x in vertical)

    intersections = [(x, y) for y in ys for x in xs if crosses_h(y, x) and crosses_v(x, y)]
    if len(intersections) < len(xs) * len(ys) * 0.6:
        return []
    table_bbox = (min(xs), min(ys), max(xs), max(ys))
    rows: list[tuple[str, ...]] = []
    for top, bottom in zip(ys, ys[1:]):
        cells = []
        for left, right in zip(xs, xs[1:]):
            cell_words = [
                str(word[4])
                for word in words
                if left <= (word[0] + word[2]) / 2 <= right and top <= (word[1] + word[3]) / 2 <= bottom
            ]
            cells.append(_clean_cell(" ".join(cell_words)))
        rows.append(tuple(cells))
    if len(rows) < MIN_ROWS or sum(bool(cell) for row in rows for cell in row) / (len(rows) * len(xs[:-1])) < MIN_FILLED_RATIO:
        return []
    return [DetectedTable(tuple(float(v) for v in table_bbox), tuple(rows), "ruled")]


def _row_cells(words: Sequence[tuple], gap: float) -> list[tuple[float, float, str, int]]:
    ordered = sorted(words, key=lambda word: word[0])
    cells: list[list] = []
    for word in ordered:
        if cells and word[0] - cells[-1][1] <= gap:
            cells[-1][1] = max(cells[-1][1], word[2])
            cells[-1][2].append(word[4])
        else:
            cells.append([word[0], word[2], [word[4]]])
    return [(cell[0], cell[1], " ".join(cell[2]), len(cell[2])) for cell in cells]


def detect_whitespace_tables(words: Sequence[tuple], exclude: Sequence[BBox] = ()) -> list[DetectedTable]:
    """Borderless tables: consecutive rows whose short cells align on shared column anchors.

    ``words`` are ``(x0, y0, x1, y1, text, ...)`` tuples in page coordinates.
    """

    def excluded(word: tuple) -> bool:
        cx, cy = (word[0] + word[2]) / 2, (word[1] + word[3]) / 2
        return any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in exclude)

    candidates = [word for word in words if not excluded(word) and str(word[4]).strip()]
    if len(candidates) < WS_MIN_ROWS * WS_MIN_COLS:
        return []
    rows: list[list[tuple]] = []
    for word in sorted(candidates, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        mid = (word[1] + word[3]) / 2
        if rows and abs(((rows[-1][0][1] + rows[-1][0][3]) / 2) - mid) <= WS_ROW_TOLERANCE:
            rows[-1].append(word)
        else:
            rows.append([word])

    parsed: list[tuple[float, float, list[tuple[float, float, str, int]]]] = []
    for row in rows:
        height = max(word[3] - word[1] for word in row)
        cells = _row_cells(row, gap=max(9.0, height * 1.1))
        parsed.append((min(w[1] for w in row), max(w[3] for w in row), cells))

    tables: list[DetectedTable] = []
    start = 0
    while start < len(parsed):
        if len(parsed[start][2]) < WS_MIN_COLS:
            start += 1
            continue
        end = start
        while (
            end + 1 < len(parsed)
            and len(parsed[end + 1][2]) >= 2
            and parsed[end + 1][0] - parsed[end][1] <= max(14.0, (parsed[end][1] - parsed[end][0]) * 1.8)
        ):
            end += 1
        run = parsed[start : end + 1]
        table = _build_whitespace_table(run)
        if table is not None:
            tables.append(table)
        start = end + 1
    return tables


def _build_whitespace_table(run) -> DetectedTable | None:
    if len(run) < WS_MIN_ROWS:
        return None
    anchors: list[list[float]] = []
    for _, _, cells in run:
        for x0, _, _, _ in cells:
            for anchor in anchors:
                if abs(sum(anchor) / len(anchor) - x0) <= WS_ANCHOR_TOLERANCE:
                    anchor.append(x0)
                    break
            else:
                anchors.append([x0])
    strong = sorted(sum(a) / len(a) for a in anchors if len(a) >= max(WS_MIN_ROWS, 0.6 * len(run)))
    if len(strong) < WS_MIN_COLS:
        return None
    rows: list[tuple[str, ...]] = []
    word_total = cell_total = 0
    for _, _, cells in run:
        row = [""] * len(strong)
        for x0, _, text, word_count in cells:
            column = max((i for i, anchor in enumerate(strong) if anchor <= x0 + WS_ANCHOR_TOLERANCE), default=0)
            row[column] = f"{row[column]} {text}".strip()
            word_total += word_count
            cell_total += 1
        rows.append(tuple(row))
    if cell_total == 0 or word_total / cell_total > WS_MAX_WORDS_PER_CELL:
        return None
    if sum(1 for row in rows for cell in row if cell) / (len(rows) * len(strong)) < 0.6:
        return None
    bbox = (
        min(cells[0][0] for _, _, cells in run),
        min(top for top, _, _ in run),
        max(cells[-1][1] for _, _, cells in run),
        max(bottom for _, bottom, _ in run),
    )
    return DetectedTable(tuple(float(v) for v in bbox), tuple(rows), "whitespace")
