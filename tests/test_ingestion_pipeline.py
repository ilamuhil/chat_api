from __future__ import annotations

import unittest
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy import MetaData, create_engine, event, select
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.ingestion.pipeline import IngestionPipeline, resolve_file_extension
from app.models.chat_db_models import Documents, EmbeddingConfigurations, Embeddings
from app.models.dashboard_db_models import Files, TrainingSources
from app.rag.embeddings import count_tokens


@compiles(JSONB, "sqlite")
def _sqlite_json_type(*args, **kwargs):
    return "JSON"


@compiles(TSVECTOR, "sqlite")
def _sqlite_search_type(*args, **kwargs):
    return "TEXT"


class IngestionPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        # Exercise real ORM writes, constraints, cascades, commits, and rollbacks.
        # PostgreSQL's generated search vector is omitted from the SQLite schema.
        self.engine = create_engine("sqlite://")

        @event.listens_for(self.engine, "connect")
        def configure_connection(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.create_function("gen_random_uuid", 0, lambda: uuid.uuid4().hex)
            connection.create_function(
                "now", 0, lambda: datetime.now(UTC).isoformat(" ")
            )

        metadata = MetaData()
        documents = Documents.__table__.to_metadata(metadata)
        documents.c.search_vector.computed = None
        documents.c.search_vector.server_default = None
        Embeddings.__table__.to_metadata(metadata)
        metadata.create_all(self.engine)
        self.chat_session = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.chat_session.close)
        self.dashboard_session = MagicMock()
        self.source = TrainingSources(
            id=uuid.uuid4(),
            bot_id=uuid.uuid4(),
            organization_id="test-org",
            type="file",
            source_value="uploads/opaque-key",
        )
        self.config = EmbeddingConfigurations(
            id=uuid.uuid4(),
            bot_id=self.source.bot_id,
            provider="openai",
            model="text-embedding-3-small",
            dimension=1536,
            min_chunk_tokens=1,
            target_chunk_tokens=40,
            max_chunk_tokens=60,
        )
        self.pipeline = IngestionPipeline(
            self.source, self.chat_session, self.dashboard_session, self.config
        )
        self.file_record = Files(
            path=self.source.source_value,
            bucket="bot-files",
            original_filename="guide.md",
            mime_type="text/markdown",
        )
        self.dashboard_session.scalars.return_value.one_or_none.return_value = (
            self.file_record
        )
        self.exists = self.enterContext(
            patch("app.ingestion.pipeline.r2_object_exists", return_value=True)
        )
        self.download = self.enterContext(
            patch("app.ingestion.pipeline.r2_download_to_path")
        )
        self.embedding_client = self.enterContext(
            patch("app.rag.embeddings.OpenAIEmbeddings")
        )
        self.embedding_client.return_value.embed_documents.side_effect = lambda texts: [
            [0.1] * 1536 for _ in texts
        ]

    def upload(self, filename: str, content: str) -> None:
        self.file_record.original_filename = filename
        self.download.side_effect = lambda _bucket, _key, path: Path(path).write_text(
            content, encoding="utf-8"
        )

    def documents(self) -> list[Documents]:
        return list(
            self.chat_session.scalars(select(Documents).order_by(Documents.chunk_index))
        )

    def test_markdown_preserves_heading_code_metadata_and_embedded_text(self) -> None:
        self.upload(
            "Guide.MD",
            '# Admissions\n\nApply online.\n\n```python\n    return "a  b"\n```',
        )
        self.pipeline.run()
        documents = self.documents()
        self.assertEqual(len(documents), 2)
        self.assertEqual([d.chunk_index for d in documents], [0, 1])
        self.assertTrue(all(d.is_active for d in documents))
        self.assertEqual(
            documents[0].metadata_json["structure"]["heading_paths"], ["Admissions"]
        )
        self.assertEqual(
            documents[1].metadata_json["structure"]["content_type"], "code"
        )
        self.assertIn('    return "a  b"', documents[1].content)
        self.assertTrue(documents[0].content.startswith("Admissions\n\n"))
        self.assertEqual(
            documents[0].metadata_json["source"],
            {"filename": "Guide.MD", "source_id": str(self.source.id)},
        )
        self.assertEqual(
            documents[0].token_count,
            count_tokens(documents[0].content, self.config.model),
        )
        self.assertEqual(
            self.embedding_client.return_value.embed_documents.call_args.args[0],
            [d.content for d in documents],
        )
        self.assertEqual(len(list(self.chat_session.scalars(select(Embeddings)))), 2)

    def test_csv_preserves_table_and_original_filename(self) -> None:
        self.upload("Fees.csv", "Course,Fee\nMath,100\nScience,200\n")
        self.pipeline.run()
        document = self.documents()[0]
        self.assertEqual(document.metadata_json["structure"]["content_type"], "table")
        self.assertEqual(document.metadata_json["source"]["filename"], "Fees.csv")
        self.assertIn("Science", document.content)

    def test_txt_splits_oversized_prose_using_configuration_tokens(self) -> None:
        self.upload(
            "Guide.txt", "Students should apply online before the deadline. " * 50
        )
        self.pipeline.run()
        documents = self.documents()
        self.assertGreater(len(documents), 1)
        self.assertTrue(
            all(d.token_count <= self.config.max_chunk_tokens for d in documents)
        )
        self.assertTrue(
            all(d.metadata_json["source"]["filename"] == "Guide.txt" for d in documents)
        )
        self.assertIn("split", documents[0].metadata_json)

    def test_url_uses_html_pipeline_and_retains_url_and_canonical_metadata(
        self,
    ) -> None:
        self.source.type = "url"
        self.source.source_value = "https://example.com/admissions"
        html = (
            '<html><head><link rel="canonical" href="https://example.com/canonical"></head><body><main><h1>Admissions</h1><p>'
            + "Students should apply online before the deadline. " * 10
            + "</p></main></body></html>"
        )
        with patch(
            "app.ingestion.html_ingestion.html_extractor.HtmlExtractor.extract",
            return_value=html,
        ) as extract:
            self.pipeline.run()
        extract.assert_called_once_with(self.source.source_value)
        for document in self.documents():
            self.assertEqual(
                document.metadata_json["source"]["url"], self.source.source_value
            )
            self.assertEqual(
                document.metadata_json["source"]["canonical_url"],
                "https://example.com/canonical",
            )
        self.download.assert_not_called()

    def test_uploaded_html_uses_shared_cleaner_and_markdown_parser(self) -> None:
        self.upload(
            "Guide.html",
            "<main><h1>Admissions</h1><p>"
            + "Apply online before the deadline. " * 10
            + "</p></main>",
        )
        self.pipeline.run()
        self.assertEqual(
            self.documents()[0].metadata_json["source"]["filename"], "Guide.html"
        )
        self.assertEqual(
            self.documents()[0].metadata_json["structure"]["heading_paths"],
            ["Admissions"],
        )

    def test_pdf_routes_through_docling_extractor_and_pdf_parser(self) -> None:
        from docling_core.types.doc.document import DoclingDocument
        from docling_core.types.doc.labels import DocItemLabel

        document = DoclingDocument(name="guide")
        document.add_heading("Admissions", level=1)
        document.add_text(
            label=DocItemLabel.TEXT,
            text="Students should apply online before the deadline.",
        )
        self.upload("Guide.pdf", "placeholder")
        with patch(
            "app.ingestion.converter_factory.DocumentConverterFactory.get_converter"
        ) as converter:
            from docling.datamodel.base_models import ConversionStatus

            converter.return_value.convert.return_value = SimpleNamespace(
                status=ConversionStatus.SUCCESS, document=document
            )
            self.pipeline.run()
        converter.return_value.convert.assert_called_once()
        self.assertEqual(
            self.documents()[0].metadata_json["source"]["filename"], "Guide.pdf"
        )
        self.assertIn("source_items", self.documents()[0].metadata_json["structure"])

    def test_docx_routes_through_docling_and_shared_markdown_parser(self) -> None:
        from docling.datamodel.base_models import ConversionStatus

        self.upload("Guide.docx", "placeholder")
        with patch(
            "app.ingestion.converter_factory.DocumentConverterFactory.get_converter"
        ) as converter:
            result = converter.return_value.convert.return_value
            result.status = ConversionStatus.SUCCESS
            result.document.export_to_markdown.return_value = (
                "# Admissions\n\nApply online."
            )
            self.pipeline.run()
        converter.return_value.convert.assert_called_once()
        self.assertEqual(
            self.documents()[0].metadata_json["structure"]["heading_paths"],
            ["Admissions"],
        )
        self.assertEqual(
            self.documents()[0].metadata_json["source"]["filename"], "Guide.docx"
        )

    def test_retry_replaces_documents_and_embeddings_without_duplicates(self) -> None:
        self.upload("Guide.md", "# Admissions\n\nApply online.")
        self.pipeline.run()
        old_id = self.documents()[0].id
        self.upload("Guide.md", "# Admissions\n\nApplications now close in June.")
        self.pipeline.run()
        self.assertEqual(len(self.documents()), 1)
        self.assertNotEqual(self.documents()[0].id, old_id)
        embeddings = list(self.chat_session.scalars(select(Embeddings)))
        self.assertEqual(len(embeddings), 1)
        self.assertEqual(embeddings[0].document_id, self.documents()[0].id)

    def test_embedding_failure_rolls_back_replacement_and_preserves_old_vectors(
        self,
    ) -> None:
        self.upload("Guide.md", "# Admissions\n\nApply online.")
        self.pipeline.run()
        old_id = self.documents()[0].id
        self.upload("Guide.md", "# Admissions\n\nReplacement content.")
        self.embedding_client.return_value.embed_documents.side_effect = RuntimeError(
            "provider unavailable"
        )
        with (
            self.assertLogs("app", level="ERROR"),
            self.assertRaisesRegex(ValueError, "Embeddings could not be generated"),
        ):
            self.pipeline.run()
        self.assertEqual(self.documents()[0].id, old_id)
        self.assertIn("Apply online", self.documents()[0].content)
        self.assertEqual(
            self.chat_session.scalars(select(Embeddings)).one().document_id, old_id
        )

    def test_incomplete_embedding_response_never_activates_documents(self) -> None:
        self.upload("Guide.md", "Apply online.")
        self.embedding_client.return_value.embed_documents.side_effect = None
        self.embedding_client.return_value.embed_documents.return_value = []
        with self.assertLogs("app", level="ERROR"), self.assertRaises(ValueError):
            self.pipeline.run()
        self.assertEqual(self.documents(), [])
        self.assertEqual(list(self.chat_session.scalars(select(Embeddings))), [])

    def test_replacement_preserves_other_configuration_documents(self) -> None:
        self.upload("Guide.md", "Apply online.")
        self.pipeline.run()
        old = self.documents()[0]
        other_config = uuid.uuid4()
        old.embedding_configuration_id = other_config
        self.chat_session.commit()
        self.pipeline.run()
        self.assertEqual(
            {d.embedding_configuration_id for d in self.documents()},
            {other_config, self.config.id},
        )

    def test_empty_source_fails_without_documents_or_embeddings(self) -> None:
        self.upload("Guide.txt", " \n\n")
        with self.assertRaisesRegex(ValueError, "no usable content"):
            self.pipeline.run()
        self.assertEqual(self.documents(), [])
        self.embedding_client.assert_not_called()

    def test_missing_storage_object_fails_before_download(self) -> None:
        self.exists.return_value = False
        with self.assertRaisesRegex(ValueError, "upload was not completed"):
            self.pipeline.run()
        self.download.assert_not_called()
        self.assertEqual(self.documents(), [])

    def test_unknown_source_type_is_rejected(self) -> None:
        self.source.type = "other"
        with self.assertRaisesRegex(ValueError, "Unsupported training source type"):
            self.pipeline.run()
        self.download.assert_not_called()

    def test_extension_resolution_and_unsupported_formats(self) -> None:
        self.assertEqual(resolve_file_extension("Guide.PDF", "text/plain"), ".pdf")
        self.assertEqual(
            resolve_file_extension(None, "text/markdown; charset=utf-8"), ".md"
        )
        self.assertEqual(
            resolve_file_extension(
                None,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
            ".docx",
        )
        with self.assertRaisesRegex(ValueError, "Unsupported file type"):
            resolve_file_extension("data.xlsx", "text/csv")


if __name__ == "__main__":
    unittest.main()
