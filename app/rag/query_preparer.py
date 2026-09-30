import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from app.helpers.rag import count_tokens
from app.rag.shared_dataclasses import ConversationTurn, PreparedQuery, RetrievalRequest

logger = logging.getLogger(__name__)


class QueryPreparer(BaseModel):
    rewrite_model: str = Field(..., description="The model to use for query rewriting.")
    rewrite_prompt: str = Field(
        default="Return only the standalone query. Preserve the user’s intent. Do not answer the question",
        description="The prompt to use for query rewriting.",
    )
    max_history_tokens: int = Field(
        ..., description="The maximum number of tokens in the history."
    )
    max_query_tokens: int = Field(
        ..., description="The maximum number of tokens in the query."
    )

    def prepare(self, request: RetrievalRequest) -> PreparedQuery:
        try:
            history = self._select_history(request.scoped_conversation_history)
            if not history:
                logger.warning("No history selected.")
                return PreparedQuery(
                    original_message=request.original_message,
                    standalone_query=request.original_message,
                    did_rewrite=False,
                )
            rewritten_query = self._rewrite(history, request.original_message)
            return PreparedQuery(
                original_message=request.original_message,
                standalone_query=rewritten_query,
                did_rewrite=True,
            )

        except ValueError as exc:
            logger.warning(
                "Query rewriting failed; using the original message: %s", exc
            )
            return PreparedQuery(
                original_message=request.original_message,
                standalone_query=request.original_message,
                did_rewrite=False,
            )

    def _select_history(
        self, history: list[ConversationTurn]
    ) -> list[ConversationTurn]:
        """Keep the newest whole turns within the content-token budget."""
        bounded_history: list[ConversationTurn] = []
        total_tokens = 0

        for turn in reversed(history):
            turn_tokens = count_tokens(turn.content, self.rewrite_model)

            if total_tokens + turn_tokens > self.max_history_tokens:
                break

            bounded_history.append(turn)
            total_tokens += turn_tokens

        return list(reversed(bounded_history))

    def _rewrite(self, history: list[ConversationTurn], original_message: str) -> str:
        rewriter = ChatOpenAI(
            model=self.rewrite_model, max_completion_tokens=self.max_query_tokens
        )
        history_text = "\n".join(f"{turn.role}: {turn.content}" for turn in history)
        response = rewriter.invoke(
            [
                SystemMessage(content=self.rewrite_prompt),
                HumanMessage(
                    content=(
                        f"# History\n{history_text}\n\n"
                        f"# User Request\n{original_message}"
                    )
                ),
            ]
        )
        if response.response_metadata.get("finish_reason") == "length":
            raise ValueError("Query rewriting reached the token limit.")

        rewritten_query = response.text.strip()

        if not rewritten_query:
            raise ValueError("Query rewriting returned an empty query.")

        return rewritten_query
