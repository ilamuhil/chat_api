from __future__ import annotations

import re
import ssl
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

_VALID_URL_RE = re.compile(
    r"^https?://"
    r"(?:"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}"
    r"|localhost"
    r"|\[(?:[0-9A-Fa-f:.]+)\]"
    r")"
    r"(?::\d{1,5})?"
    r"(?:[/?#][^\s]*)?$",
    re.IGNORECASE,
)


@dataclass
class HtmlExtractor:
    """Fetch HTML from a public HTTP(S) URL."""

    tls_context: ssl.SSLContext = field(default_factory=ssl.create_default_context)
    timeout: float = 30.0
    user_agent: str = "ChatAPI/1.0 HTML extractor"
    _client: httpx.Client | None = field(default=None, repr=False)

    def extract(self, url: str) -> str:
        """Fetch and return the raw HTML for ``url``."""
        self._validate_url(url)

        headers = {
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": self.user_agent,
        }

        def fetch(client):
            with client.stream("GET", url, headers=headers) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if content_type and "html" not in content_type:
                    raise ValueError(f"URL did not return HTML content: {content_type}")
                chunks = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > 5_000_000:
                        raise ValueError(
                            "This page is too large. Use a single page under 5 MB."
                        )
                    chunks.append(chunk)
                return b"".join(chunks).decode(
                    response.encoding or "utf-8", errors="replace"
                )

        try:
            if self._client is not None:
                return fetch(self._client)
            with httpx.Client(
                follow_redirects=True,
                max_redirects=5,
                timeout=self.timeout,
                verify=self.tls_context,
            ) as client:
                return fetch(client)
        except httpx.HTTPError as error:
            raise ValueError(f"Unable to fetch HTML from URL: {url}") from error

    @staticmethod
    def _validate_url(url: str) -> None:
        if not isinstance(url, str) or not _VALID_URL_RE.fullmatch(url):
            raise ValueError("URL must be a valid HTTP(S) URL")

        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("URL must be a valid HTTP(S) URL")
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("URL contains an invalid port") from error
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("URL contains an invalid port")
