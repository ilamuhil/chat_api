"""Bounded retries for explicitly replay-safe database operations only."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from psycopg import OperationalError
from sqlalchemy.exc import DBAPIError

logger = logging.getLogger(__name__)


def is_transient_db_error(error: Exception) -> bool:
    if isinstance(error, (TimeoutError, ConnectionError)):
        return True
    original = error.orig if isinstance(error, DBAPIError) else error
    state = getattr(original, "sqlstate", None)
    if state:
        return state.startswith("08") or state in {
            "40001",
            "40P01",
            "53300",
            "57P01",
            "57P02",
            "57P03",
        }
    if isinstance(error, DBAPIError) and error.connection_invalidated:
        return True
    if not isinstance(original, OperationalError):
        return False
    message = str(original).lower()
    return any(
        marker in message
        for marker in (
            "failed to resolve",
            "could not translate host",
            "name or service not known",
            "nodename nor servname",
            "ssl error",
            "eof detected",
            "server closed",
            "connection refused",
            "connection timed out",
            "timeout expired",
            "consuming input failed",
            "connection is closed",
            "network is unreachable",
        )
    )


async def retry_db_operation[T](
    operation: Callable[[], Awaitable[T]],
    *,
    name: str,
    attempts: int = 3,
    timeout: float = 10,
) -> T:
    """The callback must create a fresh session and own its whole transaction."""
    for attempt in range(1, attempts + 1):
        try:
            async with asyncio.timeout(timeout):
                return await operation()
        except Exception as error:
            if attempt == attempts or not is_transient_db_error(error):
                raise
            logger.warning(
                "Temporary database failure; retrying operation",
                extra={
                    "operation": name,
                    "attempt": attempt,
                    "error_type": type(error).__name__,
                },
            )
            await asyncio.sleep(0.25 * attempt)
    raise AssertionError("No database attempt made")
