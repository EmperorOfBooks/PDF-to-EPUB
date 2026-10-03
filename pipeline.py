from __future__ import annotations

import logging
from pathlib import Path

from builder import EpubBuilder
from extractor import ScannedPdfError
from router import DocumentRouter

logger = logging.getLogger("pdf2epub")


class ConversionPipeline:
    def __init__(self, input_path: str | Path, output_path: str | Path, title: str | None = None) -> None:
        self.input_path = Path(input_path).expanduser().resolve()
        self.output_path = Path(output_path).expanduser().resolve()
        self.title = title or self.input_path.stem

    def run(self) -> Path:
        logger.info("Stage 1/3: ingest and extract PDF structure from %s", self.input_path)
        logger.info("Stage 2/3: normalize reading order and chapter structure")
        cleaned = DocumentRouter().extract(self.input_path)

        logger.info("Stage 3/3: build EPUB3 package")
        result = EpubBuilder(self.output_path, title=self.title).build(cleaned)
        return result.output_path


def convert_pdf(input_path: str | Path, output_path: str | Path, title: str | None = None) -> Path:
    try:
        return ConversionPipeline(input_path=input_path, output_path=output_path, title=title).run()
    except ScannedPdfError as error:
        raise
    except Exception as error:  # pragma: no cover - explicit pass-through for CLI handling
        raise RuntimeError(f"Conversion failed: {error}") from error
