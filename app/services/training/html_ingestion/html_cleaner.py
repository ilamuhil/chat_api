import hashlib
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import ClassVar, cast

from bs4 import BeautifulSoup, Tag
from bs4.element import Comment

from app.config.logging_config import setup_logging
from app.helpers.utils import normalize_identifier, normalize_text
from app.services.training.html_ingestion.faq_parser import FAQParser

setup_logging()
logger = logging.getLogger(__name__)


@dataclass
class HTMLCleaner:
    reject_unknown_hidden: bool = True
    faq_parser: FAQParser = field(default_factory=FAQParser)

    boilerplate_selectors: tuple[str, ...] = (
        "nav",
        "[role~='navigation']",
        "body > header",
        ".site-header",
        ".topbar",
        ".utility-bar",
        "#onetrust-banner-sdk",
        "#onetrust-consent-sdk",
        "#CybotCookiebotDialog",
        "#cookie-banner",
        ".cookie-banner",
        "#cookie-consent",
        ".cookie-consent",
        ".newsletter-popup",
        "[role='dialog'][aria-hidden='true']",
        "#guidanceVideoModal",
        ".guidance-player-modal",
        "#consultModal",
        ".consult-modal",
        ".ai-finder",
        ".ai-finder-panel",
        "[class*='chat-widget']",
        "[class*='chatbot']",
        ".advertisement",
        "ins.adsbygoogle",
        "a[class*='floating']",
        "a[class*='whatsapp']",
        "a[class*='whats-new']",
        ".photo-credit",
        ".image-credit",
        "[class*='photo-credit']",
        "[class*='image-credit']",
        "nav[aria-label='breadcrumb']",
        "[aria-label='breadcrumb']",
        ".breadcrumb",
        ".breadcrumbs",
        "ol.breadcrumb",
        "[itemtype*='BreadcrumbList']",
        "[role='banner']",
        "[role='contentinfo']",
        "aside",
    )

    # Detects common inline declarations, not computed CSS visibility.
    _HIDDEN_STYLE_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"(?:^|;)\s*"
        r"(?:display\s*:\s*none|visibility\s*:\s*(?:hidden|collapse))"
        r"\s*(?:!\s*important\s*)?(?:;|$)",
        re.IGNORECASE,
    )
    _CTA_LABEL_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"^(?:"
        r"(?:log|sign)\s*in|sign\s*up|register|subscribe|"
        r"apply(?:\s+now)?|enrol(?:l)?(?:\s+now)?|"
        r"book(?:\s+(?:now|a\s+(?:call|demo|consultation)))?|"
        r"request\s+(?:a\s+)?demo|schedule\s+(?:a\s+)?call|"
        r"contact\s+us|connect\s+now|enquire\s+now|"
        r"get\s+started|learn\s+more|read\s+more|"
        r"view\s+(?:details|more)|find\s+out\s+more|"
        r"download(?:\s+(?:now|syllabus|brochure))?"
        r")$",
        re.IGNORECASE,
    )
    _LOGIN_LABEL_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"^(?:[\w&.-]+\s+){0,4}(?:crm\s+)?(?:log\s*in|login)$",
        re.IGNORECASE,
    )
    _BOLD_STYLE_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"(?:^|;)\s*font-weight\s*:\s*(?:bold|[6-9]00)\s*(?:;|$)",
        re.IGNORECASE,
    )
    _BOLD_CLASS_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"(?:^|_)(?:bold|semibold|font_bold|font_semibold|"
        r"font_weight_(?:bold|[6-9]00)|fw_[6-9]00)(?:_|$)",
        re.IGNORECASE,
    )

    def _class_names(self, tag: Tag) -> Iterable[str]:
        classes = tag.get("class") or []
        if isinstance(classes, str):
            classes = classes.split()

        for value in classes:
            yield normalize_identifier(value)

    def _has_hidden_hint(self, tag: Tag) -> bool:
        return (
            tag.has_attr("hidden")
            or str(tag.get("aria-hidden", "")).strip().lower() == "true"
            or bool(self._HIDDEN_STYLE_RE.search(str(tag.get("style", ""))))
        )

    def _controlled_panel_ids(self, root: Tag) -> set[str]:
        """Collect target IDs from controls that survived boilerplate cleanup."""
        panel_ids: set[str] = set()

        for control in root.select("[aria-controls]"):
            panel_ids.update(str(control.get("aria-controls", "")).split())

        return panel_ids

    def _is_expandable_content(
        self,
        tag: Tag,
        root: Tag,
        panel_ids: set[str],
    ) -> bool:
        """Check the node and its ancestors within the selected root."""
        current: Tag | None = tag
        while current is not None:
            if current.name == "details":
                return True
            if str(current.get("id", "")) in panel_ids:
                return True
            if current is root:
                break
            current = current.parent
        return False

    def resolve_hidden_tags(self, root: Tag) -> None:
        """
        Keep recognized expandable content.
        Reject ambiguous hidden text by default.
        """
        panel_ids = self._controlled_panel_ids(root)
        unknown_count = 0

        # Include root itself: select()/find_all() normally inspect descendants.
        for tag in [root, *root.find_all(True)]:
            if not self._has_hidden_hint(tag):
                continue
            # Ignore empty hidden wrappers.
            if not tag.get_text(" ", strip=True):
                continue
            if self._is_expandable_content(tag, root, panel_ids):
                continue
            unknown_count += 1
        if unknown_count:
            if self.reject_unknown_hidden:
                raise ValueError(
                    f"Found {unknown_count} element(s) with unexplained "
                    "hidden text; review the page's extraction rules"
                )

            logger.warning(
                "Preserving unexplained hidden text",
                extra={"hidden_element_count": unknown_count},
            )

    def get_meta_content(
        self, soup: BeautifulSoup, attr: str, value: str
    ) -> str | None:
        tag = soup.find("meta", attrs={attr: value})
        if tag is not None:
            content = tag.get("content")
            return str(content).strip() if content is not None else None
        return None

    def extract_page_metadata(self, soup: BeautifulSoup) -> dict[str, str | None]:
        title = soup.title.get_text(" ", strip=True) if soup.title is not None else None
        description = self.get_meta_content(soup, "name", "description")
        canonical = soup.select_one("link[rel~='canonical']")
        og_title = self.get_meta_content(soup, "property", "og:title")
        og_description = self.get_meta_content(soup, "property", "og:description")
        canonical_url = (
            cast(str, canonical.get("href", "")) if canonical is not None else None
        )
        language = (
            cast(str, soup.html.get("lang", "")) if soup.html is not None else None
        )
        return {
            "title": normalize_text(title) if title is not None else None,
            "description": normalize_text(description)
            if description is not None
            else None,
            "canonical_url": canonical_url,
            "og_title": normalize_text(og_title) if og_title is not None else None,
            "og_description": normalize_text(og_description)
            if og_description is not None
            else None,
            "language": normalize_text(language) if language is not None else None,
        }

    def find_content_root(self, soup: BeautifulSoup) -> Tag:
        # ! look for main content in this particular order of selectors
        selectors = [
            "main",
            "[role='main']",
            "#main-content",
            "#content",
            ".main-content",
            ".page-content",
            ".entry-content",
        ]
        for selector in selectors:
            candidate = soup.select_one(selector)
            if candidate is not None:
                return candidate
        logger.warning(
            "Little/No content root found in the HTML", extra={"html": soup.prettify()}
        )
        return soup.body or soup

    def is_worthy_content(self, stats: dict[str, int]) -> bool:
        has_structured_content = (
            stats["paragraph_count"] > 0
            or stats["heading_count"] > 0
            or stats["list_count"] > 0
            or stats["table_count"] > 0
        )
        return stats["text_length"] >= 200 and has_structured_content

    def inspect_content_root(self, root: Tag | None) -> dict[str, int]:
        if root is None:
            return {
                "text_length": 0,
                "heading_count": 0,
                "paragraph_count": 0,
                "links_count": 0,
                "list_count": 0,
                "table_count": 0,
            }
        return {
            "text_length": len(root.get_text(" ", strip=True)),
            "heading_count": len(root.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])),
            "paragraph_count": len(root.find_all("p")),
            "links_count": len(root.find_all("a")),
            "list_count": len(root.find_all(["ul", "ol", "dl"])),
            "table_count": len(root.find_all("table")),
        }

    def _remove_comments(self, soup: BeautifulSoup) -> None:
        if soup:
            for comment in soup.find_all(
                string=lambda value: isinstance(value, Comment)
            ):
                comment.extract()

    def _remove_definite_junk_tags(self, soup: BeautifulSoup) -> None:
        DEFINITE_JUNK_TAGS = [
            "script",
            "style",
            "noscript",
            "svg",
            "img",
            "iframe",
            "canvas",
            "nav",
            "footer",
            "del",
            "s",
            "strike",
            "template",
            "img",
            "input",
            "select",
            "textarea",
            "option",
        ]
        for tag in soup.find_all(DEFINITE_JUNK_TAGS):
            tag.decompose()

    def _remove_media_artifacts(self, soup: BeautifulSoup) -> None:
        """Remove short captions that describe media rather than page content."""
        media_prefixes = ("image", "photo", "credit", "pictured", "photograph")
        for caption in soup.find_all("figcaption"):
            text = normalize_text(caption.get_text(" ", strip=True))
            if not text or len(text) > 120:
                continue
            if text in {"image", "photo", "credits", "image credit"} or text.startswith(
                media_prefixes
            ):
                caption.decompose()

    def _is_visually_bold(self, tag: Tag) -> bool:
        if tag.find(["strong", "b"]) is not None:
            return True
        if self._BOLD_STYLE_RE.search(str(tag.get("style", ""))):
            return True
        return any(
            self._BOLD_CLASS_RE.search(class_name)
            for class_name in self._class_names(tag)
        )

    def _promote_visual_table_headers(self, root: Tag) -> None:
        """Promote visually bold first rows when tables omit ``th`` cells."""
        for table in root.find_all("table"):
            if table.find("th") is not None:
                continue

            first_row = table.find("tr")
            if first_row is None:
                continue
            cells = first_row.find_all("td", recursive=False)
            if not cells or not all(self._is_visually_bold(cell) for cell in cells):
                continue

            for cell in cells:
                cell.name = "th"

    def _remove_decorative_elements(self, root: Tag) -> None:
        """Remove icon-only elements before they become standalone text units."""
        candidates = root.find_all(
            ["a", "button", "div", "i", "li", "p", "span", "svg"]
        )
        for tag in reversed(candidates):
            if tag.decomposed or tag.parent is None:
                continue

            text = normalize_text(tag.get_text(" ", strip=True))
            if not text:
                continue

            role = str(tag.get("role", "")).strip().casefold()
            aria_hidden = str(tag.get("aria-hidden", "")).strip().casefold() == "true"
            identifier = " ".join(
                [
                    str(tag.get("id", "")),
                    " ".join(str(value) for value in (tag.get("class") or [])),
                ]
            ).casefold()
            is_icon_element = (
                tag.name in {"i", "svg"}
                or role in {"img", "presentation"}
                or "icon" in identifier
            )
            has_words_or_numbers = any(character.isalnum() for character in text)

            if not has_words_or_numbers or (
                len(text) <= 40 and (is_icon_element or aria_hidden)
            ):
                tag.decompose()

    def _remove_link_dense_navigation(self, root: Tag) -> None:
        """Remove navigation clusters even when a site uses generic containers."""
        candidates = root.find_all(["div", "header", "menu", "ol", "section", "ul"])
        navigation_clusters: list[Tag] = []
        for container in candidates:
            if container.decomposed or container.parent is None:
                continue

            links = container.find_all("a")
            if len(links) < 2:
                continue

            text = normalize_text(container.get_text(" ", strip=True))
            if not text or len(text) > 1500:
                continue

            link_text = normalize_text(
                " ".join(link.get_text(" ", strip=True) for link in links)
            )
            link_density = len(link_text) / len(text)
            average_chars_per_link = len(text) / len(links)
            short_link_labels = all(
                len(normalize_text(link.get_text(" ", strip=True))) <= 80
                for link in links
            )

            is_link_dense = link_density >= 0.7
            is_navigation_hub = len(links) >= 8 and average_chars_per_link <= 45
            if short_link_labels and (is_link_dense or is_navigation_hub):
                navigation_clusters.append(container)

        cluster_ids = {id(cluster) for cluster in navigation_clusters}
        for cluster in navigation_clusters:
            if any(id(parent) in cluster_ids for parent in cluster.parents):
                continue
            cluster.decompose()

    def _remove_standalone_ctas(self, root: Tag) -> None:
        """Remove short action controls and link-dominated CTA wrappers."""
        cta_re = re.compile(
            r"\b(view|click|learn more|read more|visit|download|apply|"
            r"register|enrol|book|contact|connect|enquire|get started|"
            r"find out more)\b",
            re.IGNORECASE,
        )

        for control in root.find_all(["a", "button"]):
            if control.decomposed or control.parent is None:
                continue
            label = normalize_text(control.get_text(" ", strip=True))
            if not label or len(label) > 100:
                continue
            if self._CTA_LABEL_RE.fullmatch(label) or self._LOGIN_LABEL_RE.fullmatch(
                label
            ):
                control.decompose()

        for paragraph in root.find_all(["p", "div"]):
            if paragraph.decomposed or paragraph.parent is None:
                continue
            text = normalize_text(paragraph.get_text(" ", strip=True))
            links = paragraph.find_all("a")
            link_text = normalize_text(
                " ".join(link.get_text(" ", strip=True) for link in links)
            )
            if (
                links
                and 0 < len(text) <= 120
                and len(link_text) / len(text) >= 0.75
                and cta_re.search(text)
            ):
                paragraph.decompose()

    def _remove_conditional_junk(self, root: Tag) -> None:
        """Remove identified boilerplate within the selected content root."""

        accordion_markers = {
            "accordion",
            "faq",
            "collapse",
            "toggle",
            "expand",
            "show-more",
            "read-more",
        }

        # Exact visible labels only; never substring matching.
        action_labels = {
            "log in",
            "login",
            "sign in",
            "sign up",
            "subscribe",
            "submit",
            "search",
            "share",
            "print",
            "accept cookies",
            "accept all cookies",
            "reject cookies",
            "reject all cookies",
            "manage cookies",
            "cookie settings",
        }
        removed_containers = 0
        removed_buttons = 0
        # 1. Remove explicitly identified boilerplate.
        # select() collects candidates before we mutate the tree.
        for tag in root.select(", ".join(self.boilerplate_selectors)):
            if tag.decomposed or tag is root or tag.parent is None:
                continue
            tag.decompose()
            removed_containers += 1
        # 2. Inspect remaining buttons.
        for button in root.find_all("button"):
            if button.decomposed or button.parent is None:
                continue
            # Preserve accessibility-linked controls and tab buttons.
            roles = str(button.get("role", "")).lower().split()
            if (
                button.has_attr("aria-controls")
                or button.has_attr("aria-expanded")
                or "tab" in roles
            ):
                continue
            # Preserve buttons within native expandable content.
            if button.find_parent("details") is not None:
                continue
            classes = button.get("class") or []
            if isinstance(classes, str):
                classes = classes.split()
            attributes = " ".join(
                [
                    str(button.get("id", "")),
                    " ".join(str(value) for value in classes),
                    str(button.get("aria-label", "")),
                ]
            ).lower()
            # These keywords only PRESERVE content; they never trigger deletion.
            if any(marker in attributes for marker in accordion_markers):
                continue
            label = (
                re.sub(
                    r"\s+",
                    " ",
                    button.get_text(" ", strip=True),
                )
                .strip()
                .casefold()
            )
            # Do not delete an unknown button merely because it is a button.
            if label in action_labels:
                button.decompose()
                removed_buttons += 1

        logger.debug(
            "Conditional HTML cleanup completed",
            extra={
                "removed_containers": removed_containers,
                "removed_buttons": removed_buttons,
            },
        )

    def _remove_duplicate_siblings(self, parent: Tag) -> None:
        seen: set[str] = set()

        # * We dont want to recurse the children of the children because normalized text would be same for both the children and the grandchildren

        children = parent.find_all(
            ["section", "article", "div", "aside", "main"], recursive=False
        )
        for child in children:
            text = normalize_text(child.get_text(" ", strip=True))
            if len(text) < 50:
                continue
            fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if fingerprint in seen:
                child.decompose()
            else:
                seen.add(fingerprint)

    def clean(self, html: object | None) -> tuple[Tag | None, dict[str, str | None]]:
        if not isinstance(html, str):
            raise TypeError("html must be a string")

        soup = BeautifulSoup(html, "lxml")
        page_meta = self.extract_page_metadata(soup)

        self._remove_comments(soup)
        self._remove_media_artifacts(soup)
        self._remove_definite_junk_tags(soup)

        root = self.find_content_root(soup)
        self._remove_conditional_junk(root)
        self._remove_decorative_elements(root)
        self.faq_parser.group_faq_items(soup, root)
        self._promote_visual_table_headers(root)
        self._remove_link_dense_navigation(root)
        self._remove_standalone_ctas(root)
        self._remove_duplicate_siblings(root)
        self.resolve_hidden_tags(root)

        # Keep your existing 200-character acceptance policy.
        if not self.is_worthy_content(self.inspect_content_root(root)):
            return None, page_meta

        return root, page_meta
