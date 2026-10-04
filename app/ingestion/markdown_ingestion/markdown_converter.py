from dataclasses import dataclass

from bs4 import Tag
from markdownify import MarkdownConverter as MC


@dataclass
class MarkdownConverter:
    def normalize_md(self, md: str) -> str:
        return md.replace("\r\n", "\n").replace("\r", "\n").strip("\n")

    def convert(self, root: Tag) -> str:
        INLINE_STRIP_TAGS = [
            "a",
            "b",
            "strong",
            "i",
            "em",
            "u",
            "mark",
            "small",
            "big",
            "font",
            "center",
            "span",
            "abbr",
            "cite",
            "dfn",
            "var",
            "time",
            "kbd",
            "samp",
            "code",
            "sub",
            "sup",
        ]
        converter = MC(
            heading_style="ATX",
            bullets="-",
            wrap=False,
            strip=INLINE_STRIP_TAGS,
            newline_style="BACKSLASH",
            table_infer_header=False,
        )

        return self.normalize_md(converter.convert_soup(root))
