import logging
from pathlib import Path
from typing import cast

from docling.datamodel.base_models import ConversionStatus, InputFormat
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
from docling_core.types.doc.common.content_layer import ContentLayer
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.items.node import DocItem

logger = logging.getLogger(__name__)


class PdfExtractor:
    converter: DocumentConverter

    def __init__(self):
        pdf_options = PdfPipelineOptions(
            do_ocr=True,
            do_table_structure=True,
            ocr_options=RapidOcrOptions(
                mode=OcrMode.PDF_AWARE_LAYOUT_REGIONS,
                use_cls=False,
            ),
        )

        format_options = cast(
            dict[InputFormat, FormatOption],
            {
                InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_options),
            },
        )
        converter = DocumentConverter(
            allowed_formats=[InputFormat.PDF],
            format_options=format_options,
        )

        self.converter = converter

    def convert_pdf(self, source_path: str | Path) -> DoclingDocument:
        pdf_path = Path(source_path) if isinstance(source_path, str) else source_path
        if not pdf_path.exists():
            logger.error(f"File {pdf_path} does not exist")
            raise FileNotFoundError(f"File {pdf_path} does not exist")
        if not pdf_path.is_file():
            logger.error(f"File {pdf_path} is not a file")
            raise ValueError(f"File {pdf_path} is not a file")
        if pdf_path.suffix.lower() != ".pdf":
            logger.error(f"File {pdf_path} is not a PDF file")
            raise ValueError(f"File {pdf_path} is not a PDF file")
        if pdf_path.stat().st_size == 0:
            logger.error(f"File {pdf_path} is empty")
            raise ValueError(f"File {pdf_path} is empty")
        result = self.converter.convert(pdf_path, raises_on_error=False)
        if result.status != ConversionStatus.SUCCESS:
            logger.error(
                f"Failed to convert PDF file {pdf_path}",
                extra={"status": result.status},
            )
            raise RuntimeError(
                f"Failed to convert PDF file {pdf_path}: {result.status}"
            )

        return result.document

    def inspect_pdf(self, document: DoclingDocument):
        for node, depth in document.iterate_items(
            included_content_layers=set(ContentLayer), traverse_pictures=True
        ):
            if not isinstance(node, DocItem):
                continue
            print(f"Item value: {node.label.value}")
            print(f"Item depth: {depth}")

            for provenance in node.prov:
                print(
                    f"Page no: {provenance.page_no}, bbox: {provenance.bbox}, charspan: {provenance.charspan}"
                )
