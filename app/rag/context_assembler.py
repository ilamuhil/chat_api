from pydantic import BaseModel, Field

from app.rag.embeddings import count_tokens
from app.rag.shared_dataclasses import (
    ContextBundle,
    RankedCandidate,
    SourceReference,
)


class ContextAssembler(BaseModel):
    context_model: str
    max_context_tokens: int = Field(gt=0)

    def assemble(
        self,
        candidates: list[RankedCandidate],
    ) -> ContextBundle:
        context = ""
        selected_candidates: list[RankedCandidate] = []
        source_references: list[SourceReference] = []
        evidence_notes: list[str] = []
        seen_references: set[tuple] = set()

        for ranked_candidate in candidates:
            candidate = ranked_candidate.candidate
            content = candidate.content.strip()

            if not content:
                continue

            metadata = candidate.metadata or {}
            source = metadata.get("source") or {}
            structure = metadata.get("structure") or {}
            source = source if isinstance(source, dict) else {}
            structure = structure if isinstance(structure, dict) else {}

            label = (
                source.get("filename")
                or source.get("canonical_url")
                or source.get("url")
                or str(candidate.source_id)
            )
            url = source.get("canonical_url") or source.get("url")
            heading_paths = structure.get("heading_paths") or []
            heading = " > ".join(
                part for part in heading_paths if isinstance(part, str)
            )

            raw_pages = source.get("pages") or source.get("page")
            if raw_pages is None:
                pages = []
            elif isinstance(raw_pages, list):
                pages = sorted(
                    {page for page in raw_pages if type(page) is int and page > 0}
                )
            elif type(raw_pages) is int and raw_pages > 0:
                pages = [raw_pages]
            else:
                pages = []

            citation_number = len(selected_candidates) + 1
            source_line = f"[{citation_number}] Source: {label}"

            if heading:
                source_line += f"\nSection: {heading}"
            if pages:
                source_line += f"\nPage(s): {', '.join(map(str, pages))}"

            block = f"{source_line}\n{content}\n\n"
            proposed_context = context + block

            # Count the formatted context, including source and heading labels.
            proposed_token_count = count_tokens(
                proposed_context,
                self.context_model,
            )

            if proposed_token_count > self.max_context_tokens:
                # A shorter, lower-ranked candidate may still fit.
                continue

            context = proposed_context
            selected_candidates.append(ranked_candidate)

            reference_pages = pages or [None]
            for page in reference_pages:
                reference_key = (
                    candidate.source_id,
                    candidate.document_id,
                    page,
                    url,
                )
                if reference_key in seen_references:
                    continue

                seen_references.add(reference_key)
                source_references.append(
                    SourceReference(
                        citation_number=citation_number,
                        source_id=candidate.source_id,
                        document_id=candidate.document_id,
                        label=f"[{citation_number}] {label}",
                        page=page,
                        url=url,
                    )
                )

            note = f"[{citation_number}] {label}"
            if heading:
                note += f" — {heading}"
            if pages:
                note += f" — page(s) {', '.join(map(str, pages))}"
            evidence_notes.append(note)

        return ContextBundle(
            assembled_context=context,
            selected_candidates=selected_candidates,
            source_references=source_references,
            token_count=count_tokens(context, self.context_model),
            evidence_notes=evidence_notes,
        )
