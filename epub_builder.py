"""Backward-compatible import surface for the modular EPUB builder."""

from builder import BuildResult, Builder, EpubBuilder, EPUB_CSS
from rust_bridge import build_epub_archive

__all__ = ["BuildResult", "Builder", "EpubBuilder", "EPUB_CSS", "build_epub_archive"]
