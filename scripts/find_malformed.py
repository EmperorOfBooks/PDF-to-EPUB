from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from extractor import PdfExtractor


def main() -> int:
    parser = argparse.ArgumentParser(description="Find malformed chapter headings in a PDF.")
    parser.add_argument("pdf", type=Path, help="PDF to inspect")
    args = parser.parse_args()
    document = PdfExtractor(args.pdf.expanduser().resolve()).extract()
    for page in document.pages:
        for block in page.text_blocks:
            text = block.text.strip()
            if any(marker in text for marker in ("Chapter &", "Chapter S", "Chapter 1&")):
                print(f"PAGE {page.page_number} BLOCK {text!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
