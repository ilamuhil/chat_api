from pydantic import BaseModel, Field
from pathlib import Path

class TxtExtractor(BaseModel):
    def validate_txt_path(self, file_path: str | Path) -> Path:
        txt_path = Path(file_path) if isinstance(file_path, str) else file_path
        if not txt_path.exists():
            raise FileNotFoundError(f"File {txt_path} does not exist")
        if not txt_path.is_file():
            raise ValueError(f"File {txt_path} is not a file")
        if txt_path.suffix.lower() != ".txt":
            raise ValueError(f"File {txt_path} is not a TXT file")
        return txt_path


class TxtPipeline(BaseModel):
    








