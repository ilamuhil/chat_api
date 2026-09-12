from __future__ import annotations

import unittest

import httpx

from app.services.training.html_ingestion.html_extractor import HtmlExtractor


class HtmlExtractorTests(unittest.TestCase):
    def test_extract_returns_html(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                text="<html><body>content</body></html>",
                request=request,
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        extractor = HtmlExtractor(_client=client)

        self.assertEqual(
            extractor.extract("https://example.com/course"),
            "<html><body>content</body></html>",
        )

    def test_extract_rejects_non_html_response(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "application/pdf"},
                content=b"pdf",
                request=request,
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        extractor = HtmlExtractor(_client=client)

        with self.assertRaisesRegex(ValueError, "did not return HTML"):
            extractor.extract("https://example.com/document")

    def test_extract_rejects_invalid_url(self) -> None:
        extractor = HtmlExtractor()

        with self.assertRaisesRegex(ValueError, "valid HTTP"):
            extractor.extract("not-a-url")


if __name__ == "__main__":
    unittest.main()
