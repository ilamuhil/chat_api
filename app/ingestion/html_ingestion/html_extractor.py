from __future__ import annotations

import ipaddress
import socket
import ssl
from contextlib import nullcontext
from dataclasses import dataclass, field

import httpx


@dataclass
class HtmlExtractor:
    tls_context: ssl.SSLContext = field(default_factory=ssl.create_default_context)
    timeout: float = 30.0
    user_agent: str = "ChatAPI/1.0 HTML extractor"
    _client: httpx.Client | None = field(default=None, repr=False)

    @staticmethod
    def _validate_url(url: str) -> tuple[httpx.URL, tuple[str, ...]]:
        """Return the normalized URL and its approved public IP addresses."""
        if (
            not isinstance(url, str)
            or not url
            or "\\" in url
            or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url)
        ):
            raise ValueError("URL must be a valid HTTP(S) URL")

        try:
            parsed = httpx.URL(url)
        except httpx.InvalidURL as error:
            raise ValueError("URL must be a valid HTTP(S) URL") from error

        if parsed.scheme not in {"http", "https"} or not parsed.host:
            raise ValueError("URL must be a valid HTTP(S) URL")

        if parsed.userinfo:
            raise ValueError("URL credentials are not allowed")

        if "%" in parsed.host:
            raise ValueError("Scoped or escaped host addresses are not allowed")

        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if parsed.port == 0 or not 1 <= port <= 65535:
            raise ValueError("URL contains an invalid port")

        try:
            addresses = [ipaddress.ip_address(parsed.host)]
        except ValueError:
            try:
                records = socket.getaddrinfo(
                    parsed.host,
                    port,
                    family=socket.AF_UNSPEC,
                    type=socket.SOCK_STREAM,
                    proto=socket.IPPROTO_TCP,
                )
                addresses = [ipaddress.ip_address(record[4][0]) for record in records]
            except (OSError, ValueError) as error:
                raise ValueError("URL hostname could not be resolved safely") from error

        approved = set()

        for address in addresses:
            if isinstance(address, ipaddress.IPv6Address):
                # Reject local and transition addresses that could reach IPv4.
                if (
                    address.is_site_local
                    or address.sixtofour is not None
                    or address.teredo is not None
                    or address in ipaddress.ip_network("64:ff9b::/96")
                ):
                    raise ValueError("URL must resolve only to public IP addresses")

                address = address.ipv4_mapped or address

            if not address.is_global or address.is_multicast or address.is_reserved:
                raise ValueError("URL must resolve only to public IP addresses")

            approved.add(address)

        if not approved:
            raise ValueError("URL hostname did not resolve to an IP address")

        ordered = sorted(
            approved,
            key=lambda address: (address.version, int(address)),
        )

        return (
            parsed.copy_with(fragment=None),
            tuple(str(address) for address in ordered),
        )

    def extract(self, url: str) -> str:
        """Fetch one HTML page, validating every redirect destination."""
        current_url = url
        max_redirects = 5
        redirect_codes = {301, 302, 303, 307, 308}

        client_context = (
            nullcontext(self._client)
            if self._client is not None
            else httpx.Client(
                follow_redirects=False,
                timeout=self.timeout,
                verify=self.tls_context,
                trust_env=False,
                limits=httpx.Limits(max_keepalive_connections=0),
            )
        )

        try:
            with client_context as client:
                for hop in range(max_redirects + 1):
                    original, addresses = self._validate_url(current_url)

                    headers = {
                        "Accept": "text/html,application/xhtml+xml",
                        "User-Agent": self.user_agent,
                        "Host": original.netloc.decode("ascii"),
                        "Connection": "close",
                    }

                    for index, address in enumerate(addresses):
                        # Use the validated IP instead of resolving DNS again.
                        target = original.copy_with(host=address)

                        try:
                            with client.stream(
                                "GET",
                                target,
                                headers=headers,
                                follow_redirects=False,
                                timeout=self.timeout,
                                extensions={
                                    "sni_hostname": original.raw_host.decode("ascii")
                                },
                            ) as response:
                                if response.status_code in redirect_codes:
                                    location = response.headers.get("location")

                                    if not location:
                                        raise ValueError("Redirect has no destination")

                                    if hop == max_redirects:
                                        raise ValueError("Too many redirects")

                                    # Resolve relative redirects against the
                                    # original hostname, not the pinned IP.
                                    current_url = str(original.join(location))
                                    break

                                response.raise_for_status()

                                content_type = (
                                    response.headers.get("content-type", "")
                                    .split(";", 1)[0]
                                    .strip()
                                    .lower()
                                )

                                if content_type and content_type not in {
                                    "text/html",
                                    "application/xhtml+xml",
                                }:
                                    raise ValueError("URL did not return HTML content")

                                chunks = []
                                total = 0

                                for chunk in response.iter_bytes(chunk_size=65_536):
                                    total += len(chunk)

                                    if total > 5_000_000:
                                        raise ValueError(
                                            "This page is too large. "
                                            "Use a single page under 5 MB."
                                        )

                                    chunks.append(chunk)

                                return b"".join(chunks).decode(
                                    response.encoding or "utf-8",
                                    errors="replace",
                                )

                        except (httpx.ConnectError, httpx.ConnectTimeout):
                            # Try another already-validated address.
                            if index == len(addresses) - 1:
                                raise

        except httpx.HTTPError as error:
            raise ValueError("Unable to fetch HTML from URL") from error

        raise ValueError("Too many redirects")
