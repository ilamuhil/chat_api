"""Inspect HTML knowledge units fetched from a URL.

Run from the repository root:
    python -m app.scripts.inspect_html_units SOURCE

SOURCE must be an HTTP(S) URL.
"""

import argparse
import io
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from app.services.training.html_ingestion.pipeline import HtmlIngestionPipeline
from app.services.training.knowledge_unit import KnowledgeUnit

DEFAULT_OUTPUT_DIR = Path(r"C:\Users\Ilamuhil ilavenil\OneDrive\Desktop\outputs")


def format_unit(unit: KnowledgeUnit, *, number: int, total: int) -> str:
    """Share the same complete, readable format for terminal and file output."""
    return "\n".join(
        [
            "",
            "=" * 88,
            f"KNOWLEDGE UNIT {number} / {total}",
            f"Source order: {unit.source_order}",
            f"Content type: {unit.content_type}",
            f"Page: {unit.metadata.get('source', {}).get('page', '(none)')}",
            f"Heading path: {' > '.join(unit.heading_path) or '(none)'}",
            "-" * 88,
            "CONTENT\n",
            unit.content,
            "\n" + "-" * 88,
            "METADATA\n",
            json.dumps(unit.metadata, indent=2, ensure_ascii=False),
        ]
    )


def print_unit(unit: KnowledgeUnit, *, number: int, total: int) -> None:
    """Display a unit while preserving its content whitespace."""
    print(format_unit(unit, number=number, total=total), flush=True)


def write_units_to_file(
    units: list[KnowledgeUnit],
    *,
    source_name: str,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> Path:
    """Write one UTF-8 report for the source and return its path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    stem = Path(source_name).stem or "html_source"
    output_path = output_dir / f"{stem}_knowledge_units_{timestamp}.txt"
    with output_path.open("x", encoding="utf-8") as report:
        report.write(f"HTML source: {source_name}\n")
        report.write(f"Knowledge units: {len(units)}\n")
        for number, unit in enumerate(units, start=1):
            report.write(format_unit(unit, number=number, total=len(units)))
            report.write("\n")
    return output_path


def main() -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")

    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument(
        "source",
        help="HTTP(S) URL or local .html/.htm file",
    )
    arguments.add_argument(
        "--write-file",
        action="store_true",
        help=f"Write the report to {DEFAULT_OUTPUT_DIR} instead of printing it.",
    )
    args = arguments.parse_args()

    logging.basicConfig(level=logging.WARNING)
    try:
        units = HtmlIngestionPipeline().run(args.source)
        source_name = args.source
    except Exception:
        logging.exception("Could not inspect HTML source: %s", args.source)
        return 1

    if args.write_file:
        output_path = write_units_to_file(units, source_name=source_name)
        print(f"Saved {len(units)} knowledge units to: {output_path}", flush=True)
    else:
        print(
            f"\nHTML source: {source_name}\nKnowledge units: {len(units)}", flush=True
        )
        for number, unit in enumerate(units, start=1):
            print_unit(unit, number=number, total=len(units))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
