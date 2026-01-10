from traceback import print_exception
from bs4 import BeautifulSoup
import re
import unicodedata
import os
import httpx


def extract_main_text_from_html(html: str) -> str:
    """
    - Remove boilerplate elements (nav/footer/header/aside/form/scripts).
    - Prefer <main> or <article>, else fall back to <body>.
    - Convert to text with newlines to preserve paragraph breaks.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Remove non-content tags.
    for tag in soup(["script", "style", "noscript", "svg", "img", "iframe", "canvas"]):
        tag.decompose()

    # Remove boilerplate containers.
    for tag in soup.find_all(["nav", "footer", "header", "aside", "form"]):
        tag.decompose()

    # Remove "banner/modal/cookie/login" sections by class/id heuristics.
    junk_re = re.compile(
        r"(cookie|consent|gdpr|banner|modal|popup|subscribe|sign[\s_-]?in|sign[\s_-]?up|login|register|advert|ads|promo|footer|header|nav|sidebar)",
        re.IGNORECASE,
    )
    for tag in soup.find_all(True):
        ident = " ".join(
            [
                str(tag.get("id") or ""),
                " ".join(tag.get("class") or []),
                str(tag.get("role") or ""),
            ]
        )
        if ident and junk_re.search(ident):
            tag.decompose()

    root = soup.find("main") or soup.find("article") or soup.body or soup
    text = root.get_text("\n")
    return text.strip()


def clean_scraped_text(text: str) -> str:
    # normalize unicode + newlines
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # remove zero-width / weird spaces
    text = text.replace("\u200b", "").replace("\ufeff", "")

    # collapse whitespace
    # tabs/multiple spaces -> single space
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)   # trim line-leading spaces
    # many blank lines -> max 1 blank line
    text = re.sub(r"\n{3,}", "\n\n", text)

    # drop very short/noisy lines (nav/cookie fragments)
    lines = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            lines.append("")
            continue
        if len(line) < 3:
            continue
        if re.fullmatch(r"[\W_]+", line):  # only punctuation
            continue
        lines.append(line)

    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def delete_file_from_storage(bucket: str, path: str):
    """
    Delete a file from supabase storage bucket at given bucket path
    """
    base_url = os.getenv(
        "SUPABASE_BASE_URL")  # e.g. https://<project-ref>.supabase.co
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not all([base_url, service_key]):
        raise ValueError(
            "SUPABASE_BASE_URL or SUPABASE_SERVICE_ROLE_KEY is not set")

    url = f"{base_url}/storage/v1/object/{bucket}"
    headers = {
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=30) as client:
        response = client.post(url, headers=headers, json={
                               "paths": [path]}, timeout=30)
        if response.status_code >= 400:
            raise RuntimeError(
                f"Failed to delete file from storage: {response.status_code} {response.text}")
        return True  # success


def get_signed_file_url(bucket: str, path: str, expires_in: int = 3600) -> str:
    """
    Create a signed URL for a private storage object (no DB calls).
    """
    base_url = os.getenv(
        "SUPABASE_BASE_URL")  # e.g. https://<project-ref>.supabase.co
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not all([base_url, service_key]):
        raise ValueError(
            "SUPABASE_BASE_URL or SUPABASE_SERVICE_ROLE_KEY is not set")
    url = f"{base_url}/storage/v1/object/sign/{bucket}/{path}"
    headers = {
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
    }
    payload = {"expiresIn": int(expires_in), "paths": [path]}
    with httpx.Client(timeout=30) as client:
        response = client.post(url, headers=headers, json=payload, timeout=30)
        if response.status_code >= 400:
            raise ValueError(
                f"Failed to get signed URL: {response.status_code} {response.text}")
        data = response.json()
    signed = data.get("signedURL", None)
    if not signed or not isinstance(signed, str):
        print(signed)
        raise ValueError(f"Failed to get signedURL: Unknown Error")
    return signed
