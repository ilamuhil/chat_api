import re


def normalize_text(text: str) -> str:
    """replace multiple spaces with a single space and strip the text"""
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_identifier(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    value = re.sub(r"[^a-zA-Z0-9_]+", "_", value)
    value = value.strip("_").lower()
    return value
