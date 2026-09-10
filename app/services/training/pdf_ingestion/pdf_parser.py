from typing import Any

from docling_core.transforms.chunker.doc_chunk import DocChunk
from docling_core.transforms.chunker.hierarchical_chunker import (
    ChunkingDocSerializer,
    ChunkingSerializerProvider,
)
from docling_core.transforms.chunker.hybrid_chunker import HybridChunker
from docling_core.transforms.chunker.tokenizer.openai import OpenAITokenizer
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.items.code import CodeItem
from docling_core.types.doc.items.node import DocItem
from docling_core.types.doc.items.picture.picture import PictureItem
from docling_core.types.doc.items.table.table import TableItem
from docling_core.types.doc.items.text import (
    FormulaItem,
    ListItem,
    SectionHeaderItem,
    TextItem,
    TitleItem,
)
from docling_core.types.doc.labels import DocItemLabel

from app.helpers.rag import get_tokenizer
from app.services.training.knowledge_unit import KnowledgeUnit


class NoAnchorProvider(ChunkingSerializerProvider):
    def get_serializer(self, doc: DoclingDocument) -> ChunkingDocSerializer:
        serializer = ChunkingDocSerializer(doc=doc)
        serializer.params.include_hyperlinks = False
        serializer.params.traverse_pictures = True
        return serializer


class PdfParser:
    tokenizer: OpenAITokenizer
    chunker: HybridChunker
    max_tokens: int

    def __init__(self, max_tokens: int, embedding_model: str):
        if max_tokens <= 0:
            raise ValueError("max_tokens must be greater than 0")
        self.max_tokens = max_tokens
        self.tokenizer = OpenAITokenizer(
            tokenizer=get_tokenizer(embedding_model),
            max_tokens=max_tokens,
        )

        self.chunker = HybridChunker(
            tokenizer=self.tokenizer,
            merge_peers=False,
            repeat_table_header=False,
            serializer_provider=NoAnchorProvider(),
        )

    def _item_metadata(
        self, item: DocItem, *, include_table_data: bool = False
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "ref": item.self_ref,
            "label": item.label.value,
            "content_layer": item.content_layer.value,
            "parent_ref": item.parent.cref if item.parent else None,
            "children_refs": [child.cref for child in item.children],
        }

        match item:
            case ListItem():
                metadata.update(
                    content_type="list",
                    list_kind="ordered" if item.enumerated else "unordered",
                )
            case TableItem():
                metadata.update(
                    content_type=(
                        "document_index"
                        if item.label == DocItemLabel.DOCUMENT_INDEX
                        else "table"
                    ),
                    num_rows=item.data.num_rows,
                    num_columns=item.data.num_cols,
                )
                if include_table_data:
                    metadata.update(table_info=item.data.model_dump(mode="json"))
            case SectionHeaderItem():
                metadata.update(content_type="heading", heading_level=item.level)
            case CodeItem():
                metadata.update(
                    content_type="code",
                    code_language=item.code_language.value
                    if item.code_language
                    else None,
                )
            case TitleItem():
                metadata.update(content_type="title")
            case PictureItem():
                metadata.update(
                    content_type="img", image_available=item.image is not None
                )

            case FormulaItem():
                metadata.update(content_type="formula")
            case TextItem():
                metadata.update(
                    content_type="text"
                    if item.label in {DocItemLabel.TEXT, DocItemLabel.PARAGRAPH}
                    else item.label.value
                )
            case _:
                metadata["content_type"] = "unknown"

        if isinstance(item, (TableItem, PictureItem, CodeItem)):
            metadata.update(
                caption_refs=[caption.cref for caption in item.captions],
                footnote_refs=[footnote.cref for footnote in item.footnotes],
            )

        return metadata

    def _build_unit(
        self,
        chunk: DocChunk,
        document: DoclingDocument,
        content: str,
        filename: str,
        source_order: int,
        include_table_data: bool,
    ) -> KnowledgeUnit:
        items: list[dict[str, Any]] = []
        provenance_data: dict[
            str, list[dict[str, Any]]
        ] = {}  # * {chunk item ref: [provenance items]}

        for chunk_item in chunk.meta.doc_items:
            provenance_data[chunk_item.self_ref] = [
                prov.model_dump(mode="json") for prov in chunk_item.prov
            ]
            item = chunk_item.get_ref().resolve(document)

            if not isinstance(item, DocItem):
                raise TypeError(
                    f"Expected DocItem, got {type(item)} for {chunk_item.self_ref}"
                )

            items.append(
                self._item_metadata(item, include_table_data=include_table_data)
            )

        # reformat the data to match KnowledgeUnit schema

        pages = sorted(
            {
                entry["page_no"]
                for locations in provenance_data.values()
                for entry in locations
            }
        )

        types = {item["content_type"] for item in items}

        structure = {
            "heading_path": list(chunk.meta.headings or []),
            "content_type": "mixed"
            if len(types) > 1
            else next(iter(types))
            if types
            else "text",
            "labels": [item.get("label") for item in items],
            "source_items": items,
        }

        if types == {"list"}:
            kinds = {item["list_kind"] for item in items}

            structure.update(
                list_kind=next(iter(kinds)) if len(kinds) == 1 else "mixed"
            )

        # build and return the knowledge unit corresponding to the chunk

        return KnowledgeUnit(
            content=content,
            source_order=source_order,
            metadata={
                "source": {
                    "filename": filename,
                    "page": pages[0] if pages else None,
                    "pages": pages,
                    "provenance": provenance_data,
                },
                "structure": structure,
            },
        )

    def chunk_pdf(
        self,
        document: DoclingDocument,
        *,
        filename: str,
        include_table_data: bool = False,
    ) -> list[KnowledgeUnit]:

        units: list[KnowledgeUnit] = []
        for chunk in self.chunker.chunk(dl_doc=document):
            if not isinstance(chunk, DocChunk):
                raise TypeError(f"Expected DocChunk, got {type(chunk)}")
            if not chunk.text.strip():
                continue
            content = self.chunker.contextualize(chunk=chunk)
            if self.tokenizer.count_tokens(content) > self.max_tokens:
                raise ValueError(
                    f"Content exceeds max tokens: {self.max_tokens} for chunk: {len(units)}"
                )

            units.append(
                self._build_unit(
                    chunk=chunk,
                    document=document,
                    content=content,
                    filename=filename,
                    source_order=len(units),
                    include_table_data=include_table_data,
                )
            )

        if not units:
            raise ValueError("No non empty pdf chunks were produced")

        return units
