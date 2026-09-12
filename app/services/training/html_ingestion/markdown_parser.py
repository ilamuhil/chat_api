from dataclasses import dataclass, field
from typing import Any, ClassVar

from markdown_it import MarkdownIt
from markdown_it.token import Token

from app.services.training.knowledge_unit import KnowledgeUnit


@dataclass
class MarkdownUnitParser:
    LIST_OPEN_TYPES: ClassVar[set[str]] = {"bullet_list_open", "ordered_list_open"}

    markdown_parser: MarkdownIt = field(
        default_factory=lambda: MarkdownIt("commonmark", {"html": False}).enable(
            "table"
        )
    )

    def find_close_index(self, tokens: list[Token], start: int) -> int:
        depth = 0
        for index in range(start, len(tokens)):
            depth += tokens[index].nesting
            if depth == 0 and index > start:
                return index
        raise ValueError("Unclosed Markdown list found")

    def _inline_text(self, token: Token) -> str:
        parts: list[str] = []

        for child in token.children or []:
            if child.type in {"text", "code_inline"}:
                parts.append(child.content)

            elif child.type in {"softbreak", "hardbreak"}:
                parts.append("\n")

        return "".join(parts).strip()

    def _update_heading_path(
        self, headings: dict[int, str], level: int, title: str
    ) -> list[str]:
        for heading_level in list(headings):
            if heading_level >= level:
                del headings[heading_level]
        headings[level] = title
        return [headings[key] for key in sorted(headings)]

    def print_tokens(self, tokens: list[Token]) -> None:
        for index, token in enumerate(tokens):
            print(
                f"{index}: "
                f"type={token.type!r}, "
                f"tag={token.tag!r}, "
                f"nesting={token.nesting}, "
                f"level={token.level}, "
                f"content={token.content!r}, "
                f"map={token.map}"
            )

            for child in token.children or []:
                print(
                    "    child: "
                    f"type={child.type!r}, "
                    f"content={child.content!r}, "
                    f"markup={child.markup!r}"
                )

    def _parse_table(
        self, tokens: list[Token], index: int
    ) -> tuple[int, list[str], list[list[str]]]:
        if tokens[index].type != "table_open":
            raise ValueError(
                f"Expected table_open token at index {index}, got {tokens[index].type}"
            )
        header_list: list[str] = []
        row_list: list[list[str]] = []
        while index < len(tokens):
            token = tokens[index]
            if token.type == "table_close":
                return index + 1, header_list, row_list
            if token.type == "tr_open":
                if index + 1 < len(tokens) and tokens[index + 1].type == "td_open":
                    row_list.append([])
                index += 1
                continue

            elif token.type in {"th_open", "td_open"}:
                if index + 1 >= len(tokens) or tokens[index + 1].type != "inline":
                    raise ValueError("Expected inline token inside table cell")

                content = self._inline_text(tokens[index + 1]).strip()
                if token.type == "th_open":
                    header_list.append(content)
                else:
                    if not row_list:
                        raise ValueError("Table cell encountered outside body row")
                    row_list[-1].append(content)
                index += 3
                continue
            index += 1
        raise ValueError(
            "Table parsing terminated because no table_close token was found"
        )

    def _gen_table_content(
        self, header_list: list[str], row_list: list[list[str]]
    ) -> str:
        """_summary_

        Args:
            header_list (list[str]): list of table headers extracted from the markdown tokens
            row_list (list[list[str]]): list of table rows extracted from the markdown tokens. Each row is a list of table cells (strings)

        Returns:
            str: Formatted as self explanatory lines of markdown table
            Header1: Row1Value1 | Header2: Row1Value2 | ...
            Header1: Row2Value1 | Header2: Row2Value2 | ...

        """
        table_content: list[str] = []
        for row in row_list:
            cells: list[str] = []
            for column, value in enumerate(row):
                header = (
                    header_list[column].strip() if column < len(header_list) else ""
                )
                label = header or f"Column {column + 1}"
                cells.append(f"{label}: {value}")
            table_content.append(" | ".join(cells))
        return "\n".join(table_content)

    def _build_unit(
        self,
        content: str,
        page_meta: dict[str, str | None],
        heading_path: list[str],
        content_type: str,
        source_order: int,
        **metadata: Any,
    ) -> KnowledgeUnit:
        return KnowledgeUnit(
            content=content,
            metadata={
                "source": page_meta.copy(),
                "structure": {
                    "heading_path": heading_path.copy(),
                    "content_type": content_type,
                    **metadata,
                },
            },
            source_order=source_order,
        )

    def _parse_heading(
        self,
        tokens: list[Token],
        index: int,
        headings: dict[int, str],
    ) -> tuple[int, list[str]]:
        if index + 1 >= len(tokens):
            raise ValueError("Heading token has no inline content")
        token = tokens[index]
        level = int(token.tag[1])
        title = self._inline_text(tokens[index + 1])
        return index + 3, self._update_heading_path(headings, level, title)

    def _parse_code_unit(
        self,
        token: Token,
        page_meta: dict[str, str | None],
        heading_path: list[str],
        source_order: int,
    ) -> KnowledgeUnit | None:
        if not token.content.strip():
            return None
        return self._build_unit(
            token.content,
            page_meta,
            heading_path,
            "code",
            source_order,
            info=token.info,
        )

    def _parse_blockquote_unit(
        self,
        tokens: list[Token],
        index: int,
        lines: list[str],
        page_meta: dict[str, str | None],
        heading_path: list[str],
        source_order: int,
    ) -> tuple[int, KnowledgeUnit]:
        token = tokens[index]
        if token.map is None:
            raise ValueError("Blockquote token has no source map")
        start_line, end_line = token.map
        unit = self._build_unit(
            "\n".join(lines[start_line:end_line]),
            page_meta,
            heading_path,
            "blockquote",
            source_order,
        )
        return self.find_close_index(tokens, index) + 1, unit

    def _parse_table_unit(
        self,
        tokens: list[Token],
        index: int,
        page_meta: dict[str, str | None],
        heading_path: list[str],
        source_order: int,
    ) -> tuple[int, KnowledgeUnit]:
        next_index, headers, rows = self._parse_table(tokens, index)
        unit = self._build_unit(
            self._gen_table_content(headers, rows),
            page_meta,
            heading_path,
            "table",
            source_order,
            headers=headers,
            rows=rows,
        )
        return next_index, unit

    def _parse_list_unit(
        self,
        tokens: list[Token],
        index: int,
        lines: list[str],
        headings: dict[int, str],
        page_meta: dict[str, str | None],
        heading_path: list[str],
        source_order: int,
    ) -> tuple[int, KnowledgeUnit]:
        token = tokens[index]
        if token.map is None:
            raise ValueError("List open token has no map")
        content = "\n".join(lines[token.map[0] : token.map[1]])
        current_path = [headings[level] for level in sorted(headings)]
        list_kind = "ordered" if token.type == "ordered_list_open" else "unordered"
        unit = self._build_unit(
            content,
            page_meta,
            current_path or heading_path,
            "list",
            source_order,
            list_kind=list_kind,
        )
        return self.find_close_index(tokens, index) + 1, unit

    def _parse_paragraph_unit(
        self,
        tokens: list[Token],
        index: int,
        page_meta: dict[str, str | None],
        heading_path: list[str],
        source_order: int,
    ) -> tuple[int, KnowledgeUnit]:
        if index + 1 >= len(tokens):
            raise ValueError("Paragraph token has no inline content")
        unit = self._build_unit(
            self._inline_text(tokens[index + 1]),
            page_meta,
            heading_path,
            "text",
            source_order,
        )
        return index + 3, unit

    def parse(
        self, markdown: str, page_meta: dict[str, str | None]
    ) -> list[KnowledgeUnit]:
        tokens = self.markdown_parser.parse(markdown)
        units: list[KnowledgeUnit] = []
        headings: dict[int, str] = {}
        source_order = 0
        index = 0
        lines = markdown.splitlines()
        heading_path: list[str] = []

        while index < len(tokens):
            token = tokens[index]
            unit: KnowledgeUnit | None = None
            if token.type == "heading_open":
                index, heading_path = self._parse_heading(tokens, index, headings)
                continue
            if token.type in {"fence", "code_block"}:
                unit = self._parse_code_unit(
                    token, page_meta, heading_path, source_order
                )
                index += 1
            elif token.type == "blockquote_open":
                index, unit = self._parse_blockquote_unit(
                    tokens,
                    index,
                    lines,
                    page_meta,
                    heading_path,
                    source_order,
                )
            elif token.type == "table_open":
                index, unit = self._parse_table_unit(
                    tokens,
                    index,
                    page_meta,
                    heading_path,
                    source_order,
                )
            elif token.type in self.LIST_OPEN_TYPES:
                index, unit = self._parse_list_unit(
                    tokens,
                    index,
                    lines,
                    headings,
                    page_meta,
                    heading_path,
                    source_order,
                )
            elif token.type == "paragraph_open":
                index, unit = self._parse_paragraph_unit(
                    tokens,
                    index,
                    page_meta,
                    heading_path,
                    source_order,
                )
            else:
                index += 1

            if unit is not None and unit.content.strip():
                units.append(unit)
                source_order += 1

        return [unit for unit in units if unit.content.strip()]
