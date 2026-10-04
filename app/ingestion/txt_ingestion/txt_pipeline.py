import logging
import re
from copy import deepcopy
from pathlib import Path

from pydantic import BaseModel

from app.ingestion import KnowledgeUnit, SourceType

logger = logging.getLogger(__name__)


class TxtExtractor(BaseModel):
    @staticmethod
    def validate_txt_path(file_path: str | Path) -> Path:
        txt_path = Path(file_path) if isinstance(file_path, str) else file_path
        if not txt_path.exists():
            raise FileNotFoundError(f"File {txt_path} does not exist")
        if not txt_path.is_file():
            raise ValueError(f"File {txt_path} is not a file")
        if txt_path.suffix.lower() != ".txt":
            raise ValueError(f"File {txt_path} is not a TXT file")
        return txt_path


class TxtPipeline(BaseModel):
    def convert(self, file_path: str | Path, filename: str) -> list[KnowledgeUnit]:
        try:
            validated_path = TxtExtractor.validate_txt_path(file_path)
        except Exception:
            logger.exception(
                "Text file path could not be validated",
                extra={"file_path": file_path},
                exc_info=True,
            )
            raise
        with open(validated_path, encoding="utf-8") as txt_file:
            units = []
            content = ""
            metadata = {
                "source": {"filename": filename},
                "structure": {
                    "content_type": "paragraph",
                    "heading_paths": [],
                },
            }
            for line in txt_file:
                line = re.sub(r"[ \t]+", " ", line)
                if not line.strip():
                    if content.strip():
                        units.append(
                            KnowledgeUnit(
                                content=content.strip(),
                                source_type=SourceType.TXT,
                                source_order=len(units),
                                metadata=deepcopy(metadata),
                            )
                        )
                    content = ""
                else:
                    content += line
            if content.strip():
                units.append(
                    KnowledgeUnit(
                        content=content.strip(),
                        source_type=SourceType.TXT,
                        source_order=len(units),
                        metadata=deepcopy(metadata),
                    )
                )
            return units
