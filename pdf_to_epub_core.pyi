"""Type stubs for the native Rust extension pdf_to_epub_core."""

from typing import Any

def sort_reading_order(
    blocks: list[dict[str, Any]], page_width: float | None = None
) -> list[dict[str, Any]]: ...

def detect_columns(
    blocks: list[dict[str, Any]], page_width: float
) -> list[list[dict[str, Any]]]: ...

def batch_sort_reading_order(
    pages_blocks: list[list[dict[str, Any]]], page_width: float | None = None
) -> list[list[dict[str, Any]]]: ...

def reconstruct_table_grid(
    cells: list[dict[str, Any]], tolerance: float = 3.0
) -> list[list[dict[str, Any]]]: ...

def build_epub_archive(
    manifest: list[tuple[str, bytes]],
    output_path: str,
    date_time: tuple[int, int, int, int, int, int] | None = None,
) -> None: ...
