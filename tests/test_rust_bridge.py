from __future__ import annotations

import tempfile
import time
import zipfile
from pathlib import Path

import pytest

import rust_bridge


def test_rust_core_available():
    assert rust_bridge.is_rust_available() is True


def test_spatial_layout_sorting_multicolumn():
    # 2 columns on a 600-width page: left column (x=40..250), right column (x=350..550)
    blocks = [
        {"bbox": [350.0, 100.0, 550.0, 140.0], "text": "Right top", "id": "R1"},
        {"bbox": [40.0, 200.0, 250.0, 240.0], "text": "Left bottom", "id": "L2"},
        {"bbox": [350.0, 200.0, 550.0, 240.0], "text": "Right bottom", "id": "R2"},
        {"bbox": [40.0, 100.0, 250.0, 140.0], "text": "Left top", "id": "L1"},
    ]

    sorted_blocks = rust_bridge.sort_reading_order(blocks, page_width=600.0)
    ids = [b["id"] for b in sorted_blocks]
    # Left column top-to-bottom, then right column top-to-bottom
    assert ids == ["L1", "L2", "R1", "R2"]


def test_detect_columns_partitioning():
    blocks = [
        {"bbox": [40.0, 100.0, 250.0, 140.0], "text": "Left 1"},
        {"bbox": [40.0, 150.0, 250.0, 190.0], "text": "Left 2"},
        {"bbox": [350.0, 100.0, 550.0, 140.0], "text": "Right 1"},
        {"bbox": [350.0, 150.0, 550.0, 190.0], "text": "Right 2"},
    ]

    columns = rust_bridge.detect_columns(blocks, page_width=600.0)
    assert len(columns) == 2
    assert len(columns[0]) == 2
    assert len(columns[1]) == 2
    assert [b["text"] for b in columns[0]] == ["Left 1", "Left 2"]
    assert [b["text"] for b in columns[1]] == ["Right 1", "Right 2"]


def test_reconstruct_table_grid():
    # 2 rows, 2 columns grid
    cells = [
        {"bbox": [10.0, 10.0, 100.0, 50.0], "text": "R0C0"},
        {"bbox": [105.0, 10.0, 200.0, 50.0], "text": "R0C1"},
        {"bbox": [10.0, 55.0, 100.0, 100.0], "text": "R1C0"},
        {"bbox": [105.0, 55.0, 200.0, 100.0], "text": "R1C1"},
    ]

    grid = rust_bridge.reconstruct_table_grid(cells, tolerance=5.0)
    assert len(grid) == 2
    assert len(grid[0]) == 2
    assert len(grid[1]) == 2

    assert grid[0][0]["text"] == "R0C0"
    assert grid[0][0]["row"] == 0
    assert grid[0][0]["col"] == 0
    assert grid[0][1]["text"] == "R0C1"
    assert grid[0][1]["col"] == 1

    assert grid[1][0]["text"] == "R1C0"
    assert grid[1][0]["row"] == 1
    assert grid[1][1]["text"] == "R1C1"


def test_build_epub_archive_generates_valid_ocf_container(tmp_path):
    output_file = tmp_path / "test_book.epub"
    manifest = [
        ("mimetype", b"application/epub+zip"),
        ("META-INF/container.xml", b"<container><rootfiles/></container>"),
        ("EPUB/content.opf", b"<package><metadata/></package>"),
        ("EPUB/chapter1.xhtml", b"<html><body><p>Hello Rust</p></body></html>"),
    ]

    rust_bridge.build_epub_archive(manifest, str(output_file))
    assert output_file.exists()

    with zipfile.ZipFile(output_file, "r") as archive:
        entries = archive.infolist()
        # First entry must be mimetype and uncompressed
        assert entries[0].filename == "mimetype"
        assert entries[0].compress_type == zipfile.ZIP_STORED
        assert archive.read("mimetype") == b"application/epub+zip"
        assert archive.read("EPUB/chapter1.xhtml") == b"<html><body><p>Hello Rust</p></body></html>"


def test_build_epub_archive_rejects_directory_traversal(tmp_path):
    output_file = tmp_path / "evil.epub"
    evil_manifest = [
        ("mimetype", b"application/epub+zip"),
        ("../../../evil.sh", b"malicious content"),
    ]

    with pytest.raises(ValueError):
        rust_bridge.build_epub_archive(evil_manifest, str(output_file))


def test_benchmark_rust_vs_python_throughput():
    # Generate 400 synthetic blocks across 10 pages
    pages = []
    for p in range(10):
        page_blocks = []
        for i in range(40):
            x = 40.0 if i % 2 == 0 else 350.0
            y = (i // 2) * 20.0
            page_blocks.append({"bbox": [x, y, x + 200.0, y + 18.0], "text": f"Block {p}-{i}"})
        pages.append(page_blocks)

    # Time Python fallback
    py_start = time.perf_counter()
    for _ in range(5):
        for page in pages:
            rust_bridge._py_sort_reading_order(page, 600.0)
    py_duration = time.perf_counter() - py_start

    # Time Rust implementation
    rust_start = time.perf_counter()
    for _ in range(5):
        rust_bridge.batch_sort_reading_order(pages, 600.0)
    rust_duration = time.perf_counter() - rust_start

    # Confirm Rust is faster or runs with high throughput
    assert rust_duration < py_duration or rust_duration < 0.1

