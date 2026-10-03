from __future__ import annotations

from pathlib import Path

from cleaner import CleanedDocument, PdfCleaner
from extractor import PdfExtractor
from office_extractor import OfficeExtractor


SUPPORTED_INPUT_EXTENSIONS = {".pdf", ".docx", ".odt", ".rtf", ".doc"}


class DocumentRouter:
    def __init__(self, cleaner: PdfCleaner | None = None) -> None:
        self.cleaner = cleaner or PdfCleaner()

    def extract(self, input_path: str | Path) -> CleanedDocument:
        path = Path(input_path).expanduser().resolve()
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return self.cleaner.clean(PdfExtractor(path).extract())
        if suffix in SUPPORTED_INPUT_EXTENSIONS - {".pdf"}:
            return OfficeExtractor(path).extract()
        supported = ", ".join(sorted(SUPPORTED_INPUT_EXTENSIONS))
        raise ValueError(f"Unsupported input format {suffix or '<none>'}; expected one of {supported}")


def extract_document(input_path: str | Path) -> CleanedDocument:
    return DocumentRouter().extract(input_path)
