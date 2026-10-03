from threading import Lock

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    OcrMode,
    PdfPipelineOptions,
    RapidOcrOptions,
)
from docling.document_converter import (
    DocumentConverter,
    FormatOption,
    PdfFormatOption,
)


class DocumentConverterFactory:
    """Lazily creates and reuses the process-wide Docling converter."""

    _converter: DocumentConverter | None = None
    #* any block of code inside the lock is executed one thread at a time. the second thread will wait until the first thread releases the lock.
    _lock = Lock()

    @classmethod
    def get_converter(cls) -> DocumentConverter:
        """Return the shared Docling converter instance."""
        if cls._converter is None:
            with cls._lock:
                if cls._converter is None:
                    cls._converter = cls._build_converter()
        return cls._converter

    @staticmethod
    def _build_converter() -> DocumentConverter:
        pdf_options = PdfPipelineOptions(
            ocr_options=RapidOcrOptions(
                mode=OcrMode.PDF_AWARE_LAYOUT_REGIONS,
                use_cls=False,
            ),
        )

        format_options: dict[InputFormat, FormatOption] = {
            InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_options),
        }
        return DocumentConverter(
            allowed_formats=[InputFormat.PDF, InputFormat.DOCX],
            format_options=format_options,
        )
