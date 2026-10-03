from pathlib import Path


class DocxExtractor:
    def validate_docx_path(self, file_path: str | Path) -> Path:
        docx_path = Path(file_path) if isinstance(file_path, str) else file_path
        if not docx_path.exists():
            raise FileNotFoundError(f"File {docx_path} does not exist")
        if not docx_path.is_file():
            raise ValueError(f"File {docx_path} is not a file")
        if docx_path.suffix.lower() != ".docx":
            raise ValueError(f"File {docx_path} is not a DOCX file")
        return docx_path
