from __future__ import annotations

import argparse
from pathlib import Path
import sys

from extractor import ScannedPdfError
from pipeline import ConversionPipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert a standard book PDF into a reflowable EPUB.")
    parser.add_argument("--input", required=True, help="Path to the source PDF")
    parser.add_argument("--output", required=True, help="Path to the output EPUB")
    return parser.parse_args()


def resolve_output_path(input_path: Path, output_path: Path) -> Path:
    if output_path.exists() and output_path.is_dir():
        return output_path / f"{input_path.stem}.epub"
    if output_path.suffix.lower() != ".epub":
        return output_path.with_suffix(".epub")
    return output_path


def main() -> int:
    args = parse_args()
    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        print(f"Input PDF not found: {input_path}", file=sys.stderr)
        return 1
    output_path = resolve_output_path(input_path, Path(args.output).expanduser().resolve())
    try:
        result = ConversionPipeline(input_path=input_path, output_path=output_path, title=input_path.stem).run()
        print(str(result))
        return 0
    except ScannedPdfError as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception as error:
        print(f"Conversion failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
