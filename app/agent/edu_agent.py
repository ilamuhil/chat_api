import os
from contextlib import asynccontextmanager
from urllib.parse import quote_plus

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from pydantic import SecretStr

user = os.getenv("CHAT_DB_USERNAME")
password = os.getenv("CHAT_DB_PASSWORD")
host = os.getenv("CHAT_DB_HOST")
port = os.getenv("CHAT_DB_PORT", "5432")
name = os.getenv("CHAT_DB_NAME")
openai_api_key = os.getenv("OPENAI_API_KEY")
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


@asynccontextmanager
async def initialize_agent():
    assert openai_api_key is not None, "OpenAI API key is required"
    assert password is not None, "Password is required"
    assert user is not None, "User is required"
    assert host is not None, "Host is required"
    assert port is not None, "Port is required"
    assert name is not None, "Name is required"

    db_uri = (
        f"postgresql://{user}:{quote_plus(password)}@{host}:{port}/{name}"
        f"?sslmode=require"
    )

    async with AsyncPostgresSaver.from_conn_string(db_uri) as checkpointer:
        await checkpointer.setup()

        openai_model = ChatOpenAI(
            model=MODEL,
            temperature=0,
            api_key=SecretStr(openai_api_key),
        )

        agent = create_agent(
            model=openai_model,
            tools=[],
            checkpointer=checkpointer,
            system_prompt="You are a helpful assistant that can answer questions and help with tasks.",
        )
        yield agent
