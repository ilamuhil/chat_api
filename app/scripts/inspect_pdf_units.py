"""Inspect PDF knowledge units in the terminal or in a readable text file.

Run from the repository root:
    python -m app.scripts.inspect_pdf_units
"""

import argparse
import io
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from app.services.training.knowledge_unit import KnowledgeUnit

DEFAULT_PDF = Path(
    r"C:\Users\Ilamuhil ilavenil\OneDrive\Desktop"
    r"\Representative institute documents and resources\Fitjee program brochure.pdf"
)
DEFAULT_OUTPUT_DIR = Path(
    r"C:\Users\Ilamuhil ilavenil\OneDrive\Desktop\outputs"
)


def format_unit(unit: KnowledgeUnit, *, number: int, total: int) -> str:
    """Share the same complete, readable format between terminal and file output."""
    return "\n".join([
        "",
        "=" * 88,
        f"KNOWLEDGE UNIT {number} / {total}",
        f"Source order: {unit.source_order}",
        f"Content type: {unit.content_type}",
        f"Pages: {unit.metadata.get('source', {}).get('pages', [])}",
        f"Heading path: {' > '.join(unit.heading_path) or '(none)'}",
        "-" * 88,
        "CONTENT\n",
        unit.content,
        "\n" + "-" * 88,
        "METADATA\n",
        json.dumps(unit.metadata, indent=2, ensure_ascii=False),
    ])


def print_unit(unit: KnowledgeUnit, *, number: int, total: int) -> None:
    """Display complete content and metadata, preserving content whitespace."""
    print(format_unit(unit, number=number, total=total), flush=True)


def write_units_to_file(
    units: list[KnowledgeUnit],
    *,
    pdf_path: Path,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> Path:
    """Write one UTF-8 report per PDF and return its path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output_path = output_dir / f"{pdf_path.stem}_knowledge_units_{timestamp}.txt"
    # Exclusive creation ensures a previous inspection is never overwritten.
    with output_path.open("x", encoding="utf-8") as report:
        report.write(f"PDF: {pdf_path}\nKnowledge units: {len(units)}\n")
        for number, unit in enumerate(units, start=1):
            report.write(format_unit(unit, number=number, total=len(units)))
            report.write("\n")
    return output_path


def main() -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")

    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument(
        "path", nargs="?", type=Path, default=DEFAULT_PDF,
        help="PDF file or directory; directories are searched recursively.",
    )
    arguments.add_argument("--max-tokens", type=int, default=512)
    arguments.add_argument("--model", default="text-embedding-3-small")
    arguments.add_argument(
        "--write-file", action="store_true",
        help=f"Write units to text files in {DEFAULT_OUTPUT_DIR} instead of printing them.",
    )
    arguments.add_argument(
        "--include-table-data", action="store_true",
        help="Also print full source-table cells in each contributing unit's metadata.",
    )
    args = arguments.parse_args()
    if args.max_tokens <= 0:
        arguments.error("--max-tokens must be greater than zero")

    path: Path = args.path
    if path.is_dir():
        pdfs = sorted(
            (p for p in path.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf"),
            key=lambda p: str(p).casefold(),
        )
    elif path.is_file() and path.suffix.lower() == ".pdf":
        pdfs = [path]
    else:
        arguments.error(f"Expected an existing PDF file or directory: {path}")

    if not pdfs:
        arguments.error(f"No PDF files found in: {path}")

    # Import the heavier extraction libraries only after validating arguments.
    print(f"Loading PDF pipeline for {len(pdfs)} file(s)...", file=sys.stderr, flush=True)
    from app.services.training.pdf_ingestion.pdf_extractor import PdfExtractor
    from app.services.training.pdf_ingestion.pdf_parser import PdfParser

    logging.basicConfig(level=logging.WARNING)
    extractor = PdfExtractor()
    parser = PdfParser(max_tokens=args.max_tokens, embedding_model=args.model)
    failures = 0
    total_units = 0

    for index, pdf_path in enumerate(pdfs, start=1):
        print(f"[{index}/{len(pdfs)}] Converting {pdf_path}", file=sys.stderr, flush=True)
        try:
            document = extractor.convert_pdf(pdf_path)
            units = parser.chunk_pdf(
                document,
                filename=pdf_path.name,
                include_table_data=args.include_table_data,
            )
            if args.write_file:
                output_path = write_units_to_file(units, pdf_path=pdf_path)
                print(f"Saved {len(units)} knowledge units to: {output_path}", flush=True)
            else:
                print(f"\nPDF: {pdf_path}\nKnowledge units: {len(units)}", flush=True)
                for number, unit in enumerate(units, start=1):
                    print_unit(unit, number=number, total=len(units))
        except Exception:
            failures += 1
            logging.exception("Could not inspect %s", pdf_path)
            continue

        total_units += len(units)

    print(
        f"\nFinished: {len(pdfs) - failures} PDF(s) inspected, "
        f"{total_units} knowledge unit(s), {failures} failure(s).",
        flush=True,
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
