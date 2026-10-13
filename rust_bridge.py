"""Bridge module between Python and the Rust native acceleration core (pdf_to_epub_core).

Exposes high-performance spatial layout, table grid reconstruction, and safe EPUB
container packaging with type-annotated pure-Python fallbacks.
"""

from __future__ import annotations

import os
import zipfile
from collections import defaultdict
from typing import Any

try:
    import pdf_to_epub.pdf_to_epub_core as _core  # pyright: ignore[reportMissingModuleSource]

    HAS_RUST_CORE = True
except ImportError:
    try:
        import pdf_to_epub_core as _core  # pyright: ignore[reportMissingImports]

        HAS_RUST_CORE = True
    except ImportError:
        _core = None
        HAS_RUST_CORE = False


def is_rust_available() -> bool:
    """Check if the native Rust extension is loaded and available."""
    return HAS_RUST_CORE and _core is not None


def _get_bbox(block: dict[str, Any]) -> tuple[float, float, float, float]:
    if "bbox" in block:
        b = block["bbox"]
        return float(b[0]), float(b[1]), float(b[2]), float(b[3])
    x0 = float(block.get("x0", 0.0))
    y0 = float(block.get("y0", 0.0))
    x1 = float(block.get("x1", x0))
    y1 = float(block.get("y1", y0))
    return x0, y0, x1, y1


def _py_detect_columns(blocks: list[dict[str, Any]], page_width: float) -> list[list[dict[str, Any]]]:
    non_empty = [b for b in blocks if str(b.get("text", "")).strip()]
    if not non_empty:
        return [sorted(blocks, key=lambda b: (_get_bbox(b)[1], _get_bbox(b)[0]))]

    widths = [_get_bbox(b)[2] - _get_bbox(b)[0] for b in non_empty]
    if any(w > page_width * 0.55 for w in widths):
        return [sorted(blocks, key=lambda b: (_get_bbox(b)[1], _get_bbox(b)[0]))]

    left_edges = sorted({_get_bbox(b)[0] for b in non_empty})
    if len(left_edges) < 2 or (left_edges[-1] - left_edges[0]) < max(80.0, page_width * 0.12):
        return [sorted(blocks, key=lambda b: (_get_bbox(b)[1], _get_bbox(b)[0]))]

    split = (left_edges[0] + left_edges[-1]) / 2.0
    col0 = [b for b in blocks if _get_bbox(b)[0] < split]
    col1 = [b for b in blocks if _get_bbox(b)[0] >= split]

    col0_sorted = sorted(col0, key=lambda b: (_get_bbox(b)[1], _get_bbox(b)[0]))
    col1_sorted = sorted(col1, key=lambda b: (_get_bbox(b)[1], _get_bbox(b)[0]))

    return [col0_sorted, col1_sorted]


def _py_sort_reading_order(blocks: list[dict[str, Any]], page_width: float | None = None) -> list[dict[str, Any]]:
    if not blocks:
        return []
    width = page_width if page_width is not None else max((_get_bbox(b)[2] for b in blocks), default=612.0)
    columns = _py_detect_columns(blocks, width)
    ordered: list[dict[str, Any]] = []
    for col in columns:
        ordered.extend(col)
    return ordered


def _py_reconstruct_table_grid(cells: list[dict[str, Any]], tolerance: float = 3.0) -> list[list[dict[str, Any]]]:
    if not cells:
        return []

    tol = max(0.1, tolerance)
    x_coords: list[float] = []
    y_coords: list[float] = []
    for c in cells:
        x0, y0, x1, y1 = _get_bbox(c)
        x_coords.extend([x0, x1])
        y_coords.extend([y0, y1])

    def snap(coords: list[float]) -> list[float]:
        sorted_coords = sorted(coords)
        snapped: list[float] = []
        cluster: list[float] = [sorted_coords[0]]
        for val in sorted_coords[1:]:
            mean = sum(cluster) / len(cluster)
            if abs(val - mean) <= tol:
                cluster.append(val)
            else:
                snapped.append(mean)
                cluster = [val]
        if cluster:
            snapped.append(sum(cluster) / len(cluster))
        return snapped

    x_snapped = snap(x_coords)
    y_snapped = snap(y_coords)

    def closest(boundaries: list[float], val: float) -> int:
        return min(range(len(boundaries)), key=lambda i: abs(boundaries[i] - val))

    rows_map: dict[int, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for c in cells:
        x0, y0, x1, y1 = _get_bbox(c)
        col_start = closest(x_snapped, x0)
        col_end = max(col_start + 1, closest(x_snapped, x1))
        row_start = closest(y_snapped, y0)
        row_end = max(row_start + 1, closest(y_snapped, y1))

        c["row"] = row_start
        c["col"] = col_start
        c["colspan"] = max(1, col_end - col_start)
        c["rowspan"] = max(1, row_end - row_start)

        rows_map[row_start].append((col_start, c))

    grid: list[list[dict[str, Any]]] = []
    for row_idx in sorted(rows_map.keys()):
        row_cells = [cell for _, cell in sorted(rows_map[row_idx], key=lambda x: x[0])]
        grid.append(row_cells)
    return grid


def _py_build_epub_archive(
    manifest: list[tuple[str, bytes]],
    output_path: str,
    date_time: tuple[int, int, int, int, int, int] | None = None,
) -> None:
    for name, _ in manifest:
        if not name or name.startswith(("/", "\\")) or ".." in name.replace("\\", "/").split("/"):
            raise ValueError(f"Directory traversal attempt in archive entry: {name}")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with zipfile.ZipFile(output_path, "w") as archive:
        mimetype_content = b"application/epub+zip"
        other_entries: list[tuple[str, bytes]] = []
        for name, content in manifest:
            if name == "mimetype":
                mimetype_content = content
            else:
                other_entries.append((name, content))

        info = zipfile.ZipInfo("mimetype", date_time=date_time or (1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_STORED
        archive.writestr(info, mimetype_content)

        for name, content in other_entries:
            entry = zipfile.ZipInfo(name, date_time=date_time or (1980, 1, 1, 0, 0, 0))
            is_image = any(name.endswith(ext) for ext in (".png", ".jpg", ".jpeg"))
            entry.compress_type = zipfile.ZIP_STORED if is_image else zipfile.ZIP_DEFLATED
            archive.writestr(entry, content)


def sort_reading_order(blocks: list[dict[str, Any]], page_width: float | None = None) -> list[dict[str, Any]]:
    """Sort text blocks in natural multi-column reading order."""
    if HAS_RUST_CORE and _core is not None:
        return _core.sort_reading_order(blocks, page_width)
    return _py_sort_reading_order(blocks, page_width)


def detect_columns(blocks: list[dict[str, Any]], page_width: float) -> list[list[dict[str, Any]]]:
    """Detect columns and partition blocks into ordered column groups."""
    if HAS_RUST_CORE and _core is not None:
        return _core.detect_columns(blocks, page_width)
    return _py_detect_columns(blocks, page_width)


def batch_sort_reading_order(
    pages_blocks: list[list[dict[str, Any]]], page_width: float | None = None
) -> list[list[dict[str, Any]]]:
    """Sort blocks across multiple document pages concurrently using Rayon."""
    if HAS_RUST_CORE and _core is not None:
        return _core.batch_sort_reading_order(pages_blocks, page_width)
    return [sort_reading_order(page, page_width) for page in pages_blocks]


def reconstruct_table_grid(cells: list[dict[str, Any]], tolerance: float = 3.0) -> list[list[dict[str, Any]]]:
    """Reconstruct table row/col grid and spans from bounding box coordinates."""
    if HAS_RUST_CORE and _core is not None:
        return _core.reconstruct_table_grid(cells, tolerance)
    return _py_reconstruct_table_grid(cells, tolerance)


def build_epub_archive(
    manifest: list[tuple[str, bytes]],
    output_path: str,
    date_time: tuple[int, int, int, int, int, int] | None = None,
) -> None:
    """Build an OCF-compliant EPUB container ZIP archive."""
    if HAS_RUST_CORE and _core is not None:
        _core.build_epub_archive(manifest, str(output_path), date_time)
    else:
        _py_build_epub_archive(manifest, output_path, date_time)
