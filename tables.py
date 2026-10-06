"""Table structure reconstruction from ruling lines and whitespace grid alignment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

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


def detect_ruled_tables(page) -> list[DetectedTable]:
    """Tables whose grid is drawn with ruling lines or filled cell rectangles."""
    try:
        finder = page.find_tables(strategy="lines")
    except Exception:
        return []
    tables: list[DetectedTable] = []
    for table in finder.tables:
        try:
            raw_rows = table.extract()
        except Exception:
            continue
        rows = tuple(tuple(_clean_cell(cell) for cell in row) for row in raw_rows if row is not None)
        rows = tuple(row for row in rows if any(row))
        if len(rows) < MIN_ROWS or max((len(row) for row in rows), default=0) < MIN_COLS:
            continue
        width = max(len(row) for row in rows)
        rows = tuple(row + ("",) * (width - len(row)) for row in rows)
        cells = [cell for row in rows for cell in row]
        if sum(1 for cell in cells if cell) / len(cells) < MIN_FILLED_RATIO:
            continue
        tables.append(DetectedTable(tuple(float(v) for v in table.bbox), rows, "ruled"))
    return tables


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

    ``words`` are PyMuPDF word tuples ``(x0, y0, x1, y1, text, ...)``.
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
