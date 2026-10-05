import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.ingestion.csv_ingestion.csv_parser import CsvPipeline


class CsvPipelineTests(unittest.TestCase):
    def test_empty_cells_remain_labeled_without_shifting_columns(self) -> None:
        content = "name,email,course\nIla,,AI\n"

        with tempfile.TemporaryDirectory() as directory:
            csv_path = Path(directory) / "students.csv"
            csv_path.write_text(content, encoding="utf-8")
            units = CsvPipeline(1, 100, 200).extract_csv(csv_path)

        self.assertEqual(len(units), 1)
        self.assertIn("Columns: name | email | course", units[0].content)
        self.assertIn("name: Ila | course: AI", units[0].content)
        self.assertNotIn("email: Ila", units[0].content)
        self.assertNotIn("| Ila | AI", units[0].content)

    def test_header_and_row_are_rejected_when_they_exceed_max_tokens(self) -> None:
        long_email = "x" * 30
        content = f"name,email,course\nA,a,B\nBob,{long_email},Math\n"

        def token_count(text: str, _model: str) -> int:
            if "Columns:" in text and long_email in text:
                return 100
            return 10

        with tempfile.TemporaryDirectory() as directory:
            csv_path = Path(directory) / "students.csv"
            csv_path.write_text(content, encoding="utf-8")
            with patch(
                "app.ingestion.csv_ingestion.csv_parser.count_tokens",
                side_effect=token_count,
            ):
                units = CsvPipeline(
                    min_tokens=1,
                    target_tokens=20,
                    max_tokens=40,
                    embedding_model="test",
                ).extract_csv(csv_path)

        self.assertGreaterEqual(len(units), 2)
        self.assertTrue(all(token_count(unit.content, "test") <= 40 for unit in units))
        self.assertFalse(
            any("Columns:" in unit.content and long_email in unit.content for unit in units)
        )


if __name__ == "__main__":
    unittest.main()
