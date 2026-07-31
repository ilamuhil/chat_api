import logging
import os
from contextlib import asynccontextmanager
from urllib.parse import quote_plus

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import SecretStr

user = os.getenv("CHAT_DB_USERNAME")
password = os.getenv("CHAT_DB_PASSWORD")
host = os.getenv("CHAT_DB_HOST")
port = os.getenv("CHAT_DB_PORT", "5432")
name = os.getenv("CHAT_DB_NAME")
openai_api_key = os.getenv("OPENAI_API_KEY")
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

logger = logging.getLogger(__name__)


async def check_connection(connection: AsyncConnection[DictRow]) -> None:
    await connection.execute("SELECT 1")


def require_value(value: str | None, name: str) -> str:
    if value is None or not value.strip():
        raise RuntimeError(f"{name} is required")
    return value.strip()


@asynccontextmanager
async def initialize_agent():
    db_user = require_value(user, "CHAT_DB_USERNAME")
    db_password = require_value(password, "CHAT_DB_PASSWORD")
    db_host = require_value(host, "CHAT_DB_HOST")
    db_port = require_value(port, "CHAT_DB_PORT")
    db_name = require_value(name, "CHAT_DB_NAME")
    api_key = require_value(openai_api_key, "OPENAI_API_KEY")

    db_uri = (
        f"postgresql://{db_user}:{quote_plus(db_password)}@{db_host}:{db_port}/{db_name}"
        f"?sslmode=require"
    )
    pool: AsyncConnectionPool[AsyncConnection[DictRow]] = AsyncConnectionPool(
        conninfo=db_uri,
        min_size=0,
        max_size=5,
        open=False,
        check=check_connection,
        max_idle=60,
        max_lifetime=300,
        kwargs={
            "row_factory": dict_row,
            "autocommit": True,
            "prepare_threshold": 0,
        },
    )

    try:
        await pool.open()
        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()

        openai_model = ChatOpenAI(
            model=MODEL,
            temperature=0,
            api_key=SecretStr(api_key),
        )

        agent = create_agent(
            model=openai_model,
            tools=[],
            checkpointer=checkpointer,
            system_prompt="You are a helpful assistant that can answer questions and help with tasks.",
        )
        yield agent
    except Exception:
        logger.exception("Error occurred while initializing agent")
        raise
    finally:
        await pool.close()