from collections.abc import Callable, Iterable
from typing import ClassVar

from bs4 import BeautifulSoup, Tag

from app.helpers.utils import normalize_identifier, normalize_text


class FAQParser:
    QUESTION_MIN_SCORE = 1
    ANSWER_MIN_SCORE = 1
    FAQ_QUESTION_PATTERNS: ClassVar[tuple[str, ...]] = (
        "faq_section_title",
        "accordion_header",
        "accordion_question",
        "accordion_title",
        "accordion_item_title",
        "accordion_item_title_header",
        "accordion_item_title_text",
        "faq_question",
        "faq_expand",
        "frequently_asked_question",
        "frequently_asked_question_container",
    )

    FAQ_ANSWER_PATTERNS: ClassVar[tuple[str, ...]] = (
        "accordion_content",
        "faq_content",
        "accordion_body",
        "accordion_body_text",
        "accordion_collapse",
        "faq_answer",
        "faq_answer_container",
        "answer_container",
        "answer_text",
        "frequently_asked_ans",
    )

    def _class_names(self, tag: Tag) -> Iterable[str]:
        classes = tag.get("class") or []
        if isinstance(classes, str):
            classes = classes.split()
        for value in classes:
            yield normalize_identifier(value)

    def _class_score(
        self,
        tag: Tag,
        patterns: tuple[str, ...],
    ) -> int:
        class_names = set(self._class_names(tag))
        return sum(
            1
            for pattern in patterns
            if any(
                class_name == pattern
                or class_name.startswith(f"{pattern}_")
                or class_name.endswith(f"_{pattern}")
                for class_name in class_names
            )
        )

    def _question_score(self, tag: Tag) -> int:
        score = self._class_score(
            tag,
            self.FAQ_QUESTION_PATTERNS,
        )
        if score and tag.name in {"h2", "h3", "h4", "h5", "h6"}:
            score += 1
        if score and tag.name == "button":
            score += 1
        if tag.has_attr("aria-expanded"):
            score += 2
        if tag.has_attr("aria-controls"):
            score += 2
        if tag.get_text(" ", strip=True).endswith("?"):
            score += 1
        return score

    def _answer_score(self, tag: Tag) -> int:
        score = self._class_score(
            tag,
            self.FAQ_ANSWER_PATTERNS,
        )
        if score and tag.has_attr("aria-hidden"):
            score += 1
        if score and tag.has_attr("hidden"):
            score += 1
        return score

    def _is_question(self, tag: Tag) -> bool:
        return self._question_score(tag) >= self.QUESTION_MIN_SCORE

    def _is_answer(self, tag: Tag) -> bool:
        if tag.name in {"i", "svg"}:
            return False
        return self._answer_score(tag) >= self.ANSWER_MIN_SCORE

    def _outermost_matches(
        self,
        parent: Tag,
        predicate: Callable[[Tag], bool],
    ) -> list[Tag]:
        candidates = parent.find_all(predicate)
        return [
            candidate
            for candidate in candidates
            if not any(
                ancestor is other
                for ancestor in candidate.parents
                for other in candidates
            )
        ]

    def _find_faq_item(
        self,
        question_tag: Tag,
        root: Tag,
    ) -> tuple[Tag, Tag] | None:
        for parent in question_tag.parents:
            if parent is root:
                break
            questions = self._outermost_matches(parent, self._is_question)
            answers = self._outermost_matches(parent, self._is_answer)

            if (
                len(questions) == 1
                and len(answers) == 1
                and questions[0] is question_tag
            ):
                return parent, answers[0]

        return None

    def group_faq_items(
        self,
        soup: BeautifulSoup,
        root: Tag,
    ) -> None:
        question_tags = root.find_all(self._is_question)

        matches: list[tuple[Tag, Tag, Tag]] = []
        processed_wrapper_ids: set[int] = set()
        for question_tag in question_tags:
            result = self._find_faq_item(
                question_tag,
                root,
            )
            if result is None:
                continue
            wrapper, answer_tag = result
            wrapper_id = id(wrapper)
            if wrapper_id in processed_wrapper_ids:
                continue
            processed_wrapper_ids.add(wrapper_id)
            matches.append(
                (
                    wrapper,
                    question_tag,
                    answer_tag,
                )
            )

        for wrapper, question_tag, answer_tag in matches:
            question = normalize_text(question_tag.get_text(" ", strip=True))
            answer = normalize_text(answer_tag.get_text(" ", strip=True))
            new_tag = soup.new_tag("p")
            new_tag.string = f"Question: {question} Answer: {answer}"
            wrapper.replace_with(new_tag)
