"""Render one stored RAG trace as a readable Markdown report.

Run from the chat_api repository root:
    python -m app.scripts.inspect_rag_trace --log-id <retrieval-log-uuid>
    python -m app.scripts.inspect_rag_trace --message-id <user-message-uuid> --output rag-trace.md

Uses the normal CHAT_DB_* environment variables. Read-only; it never mutates rows.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import selectors
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import select

from app.db.session import create_async_chat_db_session
from app.models.chat_db_models import Documents, Messages, RetrievalLogs


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _fence(text: str) -> str:
    """Choose a code fence longer than any run of backticks in the content."""
    longest = max((len(match.group()) for match in re.finditer(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{text}\n{fence}"


def _format_candidate(
    candidate: dict[str, Any], document: Documents | None, selected: bool
) -> str:
    doc_id = str(candidate.get("document_id", "unknown"))
    lines = [
        f"### Rank {candidate.get('final_rank', '?')} — "
        f"{'IN CONTEXT' if selected else 'retrieved, not in context'}",
        f"- Document: `{doc_id}`",
        f"- Keyword: rank={candidate.get('keyword_rank')}, score={candidate.get('keyword_score')}",
        f"- Semantic: rank={candidate.get('semantic_rank')}, score={candidate.get('semantic_score')}",
        f"- RRF score: {candidate.get('rrf_score')}",
    ]
    if document is None:
        lines.append("- **Document row not found** (deleted or unavailable).")
        return "\n".join(lines)

    metadata = document.metadata_json or {}
    source = metadata.get("source") or {}
    structure = metadata.get("structure") or {}
    lines.extend(
        [
            f"- Source: {source.get('filename') or source.get('url') or source.get('canonical_url') or document.source_id}",
            f"- Heading: {' > '.join(structure.get('heading_paths') or [])}",
            f"- Content type: {structure.get('content_type')}",
            "",
            _fence(document.content or ""),
        ]
    )
    return "\n".join(lines)


def _render_context(selected_ids: list[str], docs: dict[str, Documents]) -> str:
    """Recreate ContextAssembler's evidence block order from its selected IDs."""
    blocks: list[str] = []
    for citation_number, doc_id in enumerate(selected_ids, start=1):
        document = docs.get(doc_id)
        if document is None:
            continue
        metadata = document.metadata_json or {}
        source = metadata.get("source") or {}
        structure = metadata.get("structure") or {}
        label = (
            source.get("filename")
            or source.get("canonical_url")
            or source.get("url")
            or str(document.source_id)
        )
        heading_paths = structure.get("heading_paths") or []
        heading = " > ".join(part for part in heading_paths if isinstance(part, str))
        raw_pages = source.get("pages") or source.get("page")
        if isinstance(raw_pages, list):
            pages = sorted({p for p in raw_pages if type(p) is int and p > 0})
        elif type(raw_pages) is int and raw_pages > 0:
            pages = [raw_pages]
        else:
            pages = []
        source_line = f"[{citation_number}] Source: {label}"
        if heading:
            source_line += f"\nSection: {heading}"
        if pages:
            source_line += f"\nPage(s): {', '.join(map(str, pages))}"
        blocks.append(f"{source_line}\n{(document.content or '').strip()}\n")
    return "\n".join(blocks)


async def _load_trace(
    *, log_id: UUID | None, message_id: UUID | None
) -> tuple[Any, list[Messages], dict[str, Documents]]:
    async with create_async_chat_db_session() as session:
        if log_id is not None:
            retrieval = await session.get(RetrievalLogs, log_id)
        else:
            retrieval = await session.scalar(
                select(RetrievalLogs)
                .where(RetrievalLogs.message_id == message_id)
                .order_by(RetrievalLogs.created_at.desc(), RetrievalLogs.id)
                .limit(1)
            )
        if retrieval is None:
            raise LookupError("No retrieval log found for the supplied identifier.")

        conversation_messages: list[Messages] = []
        if retrieval.conversation_id is not None:
            conversation_messages = list(
                (
                    await session.scalars(
                        select(Messages)
                        .where(Messages.conversation_id == retrieval.conversation_id)
                        .order_by(Messages.created_at, Messages.id)
                    )
                ).all()
            )

        details = retrieval.details or {}
        diagnostics = details.get("diagnostics") or {}
        candidates = diagnostics.get("candidates") or []
        candidate_ids = [
            str(item["document_id"]) for item in candidates if item.get("document_id")
        ]
        candidate_ids.extend(
            str(value) for value in (retrieval.retrieved_document_ids or [])
        )
        candidate_ids.extend(
            str(value) for value in (retrieval.reranked_document_ids or [])
        )
        candidate_ids.extend(
            str(value) for value in details.get("selected_document_ids", [])
        )
        unique_ids = list(dict.fromkeys(candidate_ids))
        docs: dict[str, Documents] = {}
        if unique_ids:
            rows = (
                await session.scalars(
                    select(Documents).where(
                        Documents.id.in_([UUID(value) for value in unique_ids])
                    )
                )
            ).all()
            docs = {str(document.id): document for document in rows}
        return retrieval, conversation_messages, docs


def render_report(
    retrieval: Any,
    messages: list[Messages],
    docs: dict[str, Documents],
    *,
    include_content: bool = True,
) -> str:
    details = retrieval.details or {}
    prepared = details.get("prepared_query") or {}
    diagnostics = details.get("diagnostics") or {}
    candidates = diagnostics.get("candidates") or []
    selected_ids = [str(value) for value in details.get("selected_document_ids", [])]
    selected_set = set(selected_ids)

    lines = [
        "# RAG trace",
        "",
        "## Request",
        f"- Retrieval log: `{retrieval.id}`",
        f"- Message ID: `{retrieval.message_id or 'not recorded'}`",
        f"- Conversation ID: `{retrieval.conversation_id or 'not recorded'}`",
        f"- Logged at: {retrieval.created_at}",
        f"- Original question: {prepared.get('original_message') or '(not in log)'}",
        f"- Query used: {retrieval.query or prepared.get('standalone_query') or '(not recorded)'}",
        f"- Rewritten: {prepared.get('did_rewrite')}",
        f"- Rewrite fallback: {prepared.get('fallback_reason')}",
        "",
        "## Retrieval configuration and stage counts",
        f"- Status: {details.get('status', 'unknown')}",
        f"- Embedding configuration: `{retrieval.embedding_configuration_id}`",
        f"- Bot configuration: `{retrieval.llm_configuration_id}`",
        f"- Threshold: {retrieval.retrieval_threshold}; k: {retrieval.retrieval_k}",
        f"- Keyword candidates: {diagnostics.get('keyword_candidate_count', 'not recorded')}",
        f"- Semantic candidates: {diagnostics.get('semantic_candidate_count', 'not recorded')}",
        f"- Ranked candidates: {diagnostics.get('ranked_candidate_count', len(candidates))}",
        f"- Context-selected candidates: {diagnostics.get('selected_candidate_count', len(selected_ids))}",
        f"- Context tokens: {diagnostics.get('context_tokens', 'not recorded')} / {diagnostics.get('max_context_tokens', 'not recorded')}",
        f"- Fusion: {diagnostics.get('fusion')}; RRF k: {diagnostics.get('rrf_k')}",
        f"- No-evidence reason: {diagnostics.get('no_evidence_reason')}",
    ]
    if details.get("error"):
        lines.extend(["", "## Retrieval error", _fence(_json(details["error"]))])

    lines.extend(["", "## Candidate ranking"])
    if not candidates:
        lines.append("No per-candidate ranking details were stored in this log.")
    else:
        for candidate in candidates:
            doc_id = str(candidate.get("document_id", ""))
            if include_content:
                lines.extend(
                    [
                        "",
                        _format_candidate(
                            candidate, docs.get(doc_id), doc_id in selected_set
                        ),
                    ]
                )
            else:
                lines.extend(
                    [
                        "",
                        f"### Rank {candidate.get('final_rank', '?')} — {'IN CONTEXT' if doc_id in selected_set else 'retrieved, not in context'}",
                        f"- Document: `{doc_id}`",
                        f"- Keyword: rank={candidate.get('keyword_rank')}, score={candidate.get('keyword_score')}",
                        f"- Semantic: rank={candidate.get('semantic_rank')}, score={candidate.get('semantic_score')}",
                        f"- RRF score: {candidate.get('rrf_score')}",
                    ]
                )

    lines.extend(["", "## Context supplied to answer model"])
    if include_content:
        lines.append(
            _fence(_render_context(selected_ids, docs))
            if selected_ids
            else "(No context was selected.)"
        )
    else:
        lines.append(f"Selected document IDs: {', '.join(selected_ids) or '(none)'}")

    lines.extend(["", "## Nearby conversation messages"])
    if retrieval.message_id:
        triggering = next(
            (m for m in messages if str(m.id) == str(retrieval.message_id)), None
        )
        if triggering is not None:
            lines.extend(
                [
                    "### Linked triggering message",
                    f"- Role: {triggering.role}",
                    _fence(triggering.content or ""),
                ]
            )
        else:
            lines.append("The linked message ID was not found in the messages table.")
    else:
        lines.append(
            "This retrieval log has no message_id; nearby messages are shown by timestamp as a fallback."
        )

    if retrieval.created_at:
        before = [
            m for m in messages if m.created_at and m.created_at <= retrieval.created_at
        ]
        after = [
            m for m in messages if m.created_at and m.created_at > retrieval.created_at
        ]
        user_message = next((m for m in reversed(before) if m.role == "user"), None)
        assistant_message = next((m for m in after if m.role == "ai"), None)
        for label, message in [
            ("Nearest user message", user_message),
            ("First assistant message after retrieval", assistant_message),
        ]:
            if message is not None:
                lines.extend(
                    [
                        "",
                        f"### {label} ({message.created_at})",
                        _fence(message.content or ""),
                    ]
                )

    lines.extend(
        [
            "",
            "## Trace limits",
            "- The selected context above is reconstructed from stored document rows and selected IDs; the exact prompt sent to the answer model is not stored in RetrievalLogs.",
            "- The final assistant message is matched by conversation and timestamp unless the application stores an explicit response/message link.",
            "- Only the top-k candidates stored in this retrieval log are available here. Rejected semantic matches and keyword matches below k cannot be recovered from this log.",
            "- Review the report for private user or source content before sharing it.",
            "",
        ]
    )
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print a readable trace for one RAG retrieval."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--log-id", type=UUID, help="RetrievalLogs.id")
    group.add_argument("--message-id", type=UUID, help="RetrievalLogs.message_id")
    parser.add_argument(
        "--output", type=Path, help="Write Markdown to this file instead of stdout."
    )
    parser.add_argument(
        "--hide-content",
        action="store_true",
        help="Omit document and chat message bodies.",
    )
    args = parser.parse_args()

    try:
        retrieval, messages, docs = await _load_trace(
            log_id=args.log_id, message_id=args.message_id
        )
        report = render_report(
            retrieval, messages, docs, include_content=not args.hide_content
        )
    except (LookupError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"Could not load RAG trace: {error}", file=sys.stderr)
        return 1

    if args.output:
        args.output.write_text(report, encoding="utf-8")
        print(f"Wrote RAG trace to {args.output}")
    else:
        print(report)
    return 0


def _run_main() -> int:
    if sys.platform == "win32":
        return asyncio.run(
            main(),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
    return asyncio.run(main())


if __name__ == "__main__":
    raise SystemExit(_run_main())
