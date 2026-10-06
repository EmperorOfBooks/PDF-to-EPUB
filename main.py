from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from extractor import ScannedPdfError
from office_extractor import OfficeExtractionError
from pipeline import ConversionPipeline
from telemetry import recall_gate_failed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert PDF and office documents into reflowable EPUB3.")
    parser.add_argument("--input", required=True, help="Path to a .pdf, .docx, .odt, .rtf, or .doc source")
    parser.add_argument("--output", required=True, help="Path to the output EPUB")
    parser.add_argument("--report", help="Path for the conversion audit JSON (default: alongside the EPUB)")
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
        print(f"Input file not found: {input_path}", file=sys.stderr)
        return 1
    output_path = resolve_output_path(input_path, Path(args.output).expanduser().resolve())
    try:
        report_path = Path(args.report).expanduser().resolve() if args.report else output_path.with_suffix(".report.json")
        result = ConversionPipeline(
            input_path=input_path,
            output_path=output_path,
            title=input_path.stem,
            report_path=report_path,
        ).run()
        print(str(result))
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if recall_gate_failed(report):
            print(
                f"Word recall {report['words']['recall']:.4f} is below "
                f"the required {report['words']['recall_threshold']:.2f}.",
                file=sys.stderr,
            )
            return 1
        return 0
    except ScannedPdfError as error:
        print(str(error), file=sys.stderr)
        return 2
    except OfficeExtractionError as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception as error:
        print(f"Conversion failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
