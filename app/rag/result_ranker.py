from pydantic import BaseModel, Field

from app.rag.shared_dataclasses import RankedCandidate, RetrievalCandidate


class ResultRanker(BaseModel):
    rrf_k: int = Field(default=60, gt=0, description="Smoothing factor for RRF.")
    max_candidates: int = Field(
        default=100, gt=0, description="Maximum number of candidates to rank."
    )

    def rank(
        self,
        keyword_candidates: list[RetrievalCandidate],
        semantic_candidates: list[RetrievalCandidate],
    ) -> list[RankedCandidate]:
        """Rank the union of keyword and semantic candidates using RRF."""
        keyword_by_id = {
            candidate.document_id: candidate for candidate in keyword_candidates
        }
        semantic_by_id = {
            candidate.document_id: candidate for candidate in semantic_candidates
        }
        keyword_positions = {
            candidate.document_id: candidate.keyword_rank or position
            for position, candidate in enumerate(keyword_candidates, start=1)
        }
        semantic_positions = {
            candidate.document_id: candidate.semantic_rank or position
            for position, candidate in enumerate(semantic_candidates, start=1)
        }
        document_ids = keyword_by_id.keys() | semantic_by_id.keys()

        fused: list[tuple[float, RetrievalCandidate]] = []
        for document_id in document_ids:
            keyword_candidate = keyword_by_id.get(document_id)
            semantic_candidate = semantic_by_id.get(document_id)

            keyword_rank = (
                keyword_positions.get(document_id)
                if keyword_candidate is not None
                else None
            )
            semantic_rank = (
                semantic_positions.get(document_id)
                if semantic_candidate is not None
                else None
            )

            candidate = keyword_candidate or semantic_candidate
            if candidate is None:
                continue
            candidate = candidate.model_copy(
                update={
                    "keyword_rank": keyword_rank,
                    "semantic_rank": semantic_rank,
                    "keyword_score": (
                        keyword_candidate.keyword_score
                        if keyword_candidate is not None
                        else None
                    ),
                    "semantic_score": (
                        semantic_candidate.semantic_score
                        if semantic_candidate is not None
                        else None
                    ),
                }
            )

            rrf_score = 0.0
            if keyword_rank is not None:
                rrf_score += 1 / (self.rrf_k + keyword_rank)
            if semantic_rank is not None:
                rrf_score += 1 / (self.rrf_k + semantic_rank)
            fused.append((rrf_score, candidate))

        fused.sort(key=lambda item: (-item[0], str(item[1].document_id)))
        return [
            RankedCandidate(
                rrf_score=score,
                rrf_rank=rank,
                final_rank=rank,
                candidate=candidate,
            )
            for rank, (score, candidate) in enumerate(
                fused[: self.max_candidates], start=1
            )
        ]
