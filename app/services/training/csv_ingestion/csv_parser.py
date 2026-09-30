import csv
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from app.config.logging_config import setup_logging
from app.helpers.rag import count_tokens
from app.helpers.utils import normalize_text
from app.services.training.knowledge_unit import KnowledgeUnit, SourceType

setup_logging()
logger = logging.getLogger(__name__)


@dataclass
class CsvPipeline:
    MIN_TARGET_TOKENS: int = field(default=300)
    TARGET_TOKENS: int = field(default=600)
    MAX_TOKENS: int = field(default=700)
    embedding_model: str = field(default="text-embedding-3-small")

    def _validate_csv_path(self, file_path: str | Path) -> Path:
        csv_path = Path(file_path) if isinstance(file_path, str) else file_path
        if not csv_path.exists():
            logger.error(f"File {csv_path} does not exist")
            raise FileNotFoundError(f"File {csv_path} does not exist")
        if not csv_path.is_file():
            logger.error(f"File {csv_path} is not a file")
            raise ValueError(f"File {csv_path} is not a file")
        if csv_path.suffix.lower() != ".csv":
            logger.error(f"File {csv_path} is not a CSV file")
            raise ValueError(f"File {csv_path} is not a CSV file")
        if csv_path.stat().st_size == 0:
            logger.error(f"File {csv_path} is empty")
            raise ValueError(f"File {csv_path} is empty")
        return csv_path

    def extract_csv(self, csv_path: Path) -> list[KnowledgeUnit]:
        with open(csv_path, newline="", encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)
            normalized_headers = self._validate_headers(reader.fieldnames)
            units: list[KnowledgeUnit] = []
            header_content = "Columns: " + " | ".join(normalized_headers)
            candidate = header_content
            candidate_columns = normalized_headers.copy()
            for row_number, row in enumerate(reader, start=2):
                # DictReader stores extra values under a None key.
                if None in row:
                    raise ValueError(
                        f"Malformed CSV at row {row_number}: "
                        "row contains more values than the header"
                    )
                # Skip rows where every value is empty/whitespace.

                if all(
                    value is None or normalize_text(value) == ""
                    for value in row.values()
                ):
                    continue
                cleaned_row = {
                    normalize_text(key): normalized_value
                    for key, value in row.items()
                    if (normalized_value := normalize_text(value or ""))
                }
                if not cleaned_row:
                    continue
                row_content = " | ".join(f"{value}" for value in cleaned_row.values())
                row_columns = list(cleaned_row)
                row_tokens = count_tokens(
                    row_content,
                    self.embedding_model,
                )
                if row_tokens > self.MAX_TOKENS:
                    if candidate != header_content:
                        units.append(
                            self._build_unit(
                                candidate,
                                candidate_columns,
                                len(units),
                            )
                        )
                    candidate = header_content
                    candidate_columns = normalized_headers.copy()
                    oversized_units = self._split_oversized_row(
                        cleaned_row,
                        source_order_start=len(units),
                    )
                    units.extend(oversized_units)
                    continue
                proposed = f"{candidate}\n{row_content}"
                proposed_tokens = count_tokens(
                    proposed,
                    self.embedding_model,
                )
                candidate_tokens = count_tokens(
                    candidate,
                    self.embedding_model,
                )
                can_append = proposed_tokens <= self.TARGET_TOKENS or (
                    candidate_tokens < self.MIN_TARGET_TOKENS
                    and proposed_tokens <= self.MAX_TOKENS
                )
                if can_append:
                    candidate = proposed
                    candidate_columns = list(
                        dict.fromkeys(
                            [
                                *candidate_columns,
                                *row_columns,
                            ]
                        )
                    )

                    continue
                if candidate == header_content:
                    units.extend(
                        self._split_oversized_row(
                            cleaned_row,
                            source_order_start=len(units),
                        )
                    )
                    candidate = header_content
                    candidate_columns = normalized_headers.copy()
                    continue
                units.append(
                    self._build_unit(
                        candidate,
                        candidate_columns,
                        len(units),
                    )
                )
                candidate = f"{header_content}\n{row_content}"
                candidate_columns = list(
                    dict.fromkeys([*normalized_headers, *row_columns])
                )
            if candidate != header_content:
                units.append(
                    self._build_unit(
                        candidate,
                        candidate_columns,
                        len(units),
                    )
                )

        return units

    def _validate_headers(
        self,
        fieldnames: Sequence[str] | None,
    ) -> list[str]:
        if not fieldnames:
            raise ValueError("CSV has no header row")

        normalized_headers = [normalize_text(header or "") for header in fieldnames]

        if any(not header for header in normalized_headers):
            raise ValueError("CSV contains an empty header")

        if len(normalized_headers) != len(set(normalized_headers)):
            raise ValueError("CSV contains duplicate headers")
        return normalized_headers

    def _split_oversized_row(
        self,
        row: dict[str, str],
        source_order_start: int,
    ) -> list[KnowledgeUnit]:
        units: list[KnowledgeUnit] = []
        current_content = ""
        current_columns: list[str] = []
        for key, value in row.items():
            field_content = f"{key}: {value}"

            field_tokens = count_tokens(
                field_content,
                self.embedding_model,
            )
            if field_tokens > self.MAX_TOKENS:
                raise ValueError(
                    f"CSV field '{key}' exceeds the maximum "
                    f"allowed size of {self.MAX_TOKENS} tokens"
                )
            proposed = (
                field_content
                if not current_content
                else f"{current_content} | {field_content}"
            )
            proposed_tokens = count_tokens(
                proposed,
                self.embedding_model,
            )
            if proposed_tokens <= self.MAX_TOKENS:
                current_content = proposed
                current_columns.append(key)
                continue
            units.append(
                self._build_unit(
                    current_content,
                    current_columns,
                    source_order_start + len(units),
                )
            )
            current_content = field_content
            current_columns = [key]

        if current_content:
            units.append(
                self._build_unit(
                    current_content,
                    current_columns,
                    source_order_start + len(units),
                )
            )
        return units

    def _build_unit(
        self,
        content: str,
        columns: list[str],
        source_order: int,
    ) -> KnowledgeUnit:
        return KnowledgeUnit(
            source_type=SourceType.CSV,
            content=content,
            metadata={
                "structure": {
                    "content_type": "table",
                    "columns": columns,
                },
                "token_count": count_tokens(
                    content,
                    self.embedding_model,
                ),
            },
            source_order=source_order,
        )
