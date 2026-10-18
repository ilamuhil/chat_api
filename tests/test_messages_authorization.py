from __future__ import annotations

import time
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Column, MetaData, String, Table, Uuid, create_engine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import StaticPool

from app.api.middleware.jwt import verify_jwt_middleware
from app.api.routes.messages import router
from app.db.session import get_async_chat_db


class MessageAuthorizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.public_key = cls.key.public_key().public_bytes(
            Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
        )

    def setUp(self):
        self.conversation_id = uuid4()
        self.bot_id = uuid4()
        self.organization_id = "test-org"
        self.path = f"/api/conversations/{self.conversation_id}/messages"

        # Execute the real ownership query against isolated rows, without a live DB.
        self.engine = create_engine(
            "sqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        self.addCleanup(self.engine.dispose)
        self.connection = self.engine.connect()
        self.addCleanup(self.connection.close)
        self.connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS public")
        metadata = MetaData()
        self.conversations = Table(
            "conversations_meta",
            metadata,
            Column("id", Uuid, primary_key=True),
            Column("organization_id", String),
            Column("bot_id", Uuid),
            schema="public",
        )
        metadata.create_all(self.connection)
        self.connection.execute(
            self.conversations.insert(),
            {
                "id": self.conversation_id,
                "organization_id": self.organization_id,
                "bot_id": self.bot_id,
            },
        )
        self.dashboard = AsyncMock(spec=AsyncSession)
        self.dashboard.scalar.side_effect = self.connection.scalar
        factory = self.enterContext(
            patch("app.api.routes.messages.create_async_dashboard_db_session")
        )
        factory.return_value.__aenter__ = AsyncMock(return_value=self.dashboard)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)
        self.factory = factory

        self.chat = AsyncMock(spec=AsyncSession)
        message = SimpleNamespace(
            id=uuid4(),
            conversation_id=self.conversation_id,
            created_at=datetime.now(UTC),
            agent_id=None,
            content_type="text",
            content="Synthetic private transcript",
            role="user",
        )
        result = MagicMock()
        result.all.return_value = [message]
        self.chat.scalars.return_value = result

        app = FastAPI()
        app.middleware("http")(verify_jwt_middleware)
        app.include_router(router, prefix="/api")

        async def chat_db():
            yield self.chat

        app.dependency_overrides[get_async_chat_db] = chat_db
        self.enterContext(
            patch("app.core.jwt.get_public_key", return_value=self.public_key)
        )
        self.client = self.enterContext(TestClient(app))

    def token(self, **overrides):
        now = int(time.time())
        claims = {
            "organization_id": self.organization_id,
            "bot_id": str(self.bot_id),
            "conversation_id": str(self.conversation_id),
            "type": "user",
            "iat": now,
            "exp": now + 300,
            "iss": "dashboard-server",
            "aud": "chat-server",
            **overrides,
        }
        return jwt.encode(
            {key: value for key, value in claims.items() if value is not None},
            self.key,
            algorithm="RS256",
        )

    def get(self, **claims):
        return self.client.get(
            self.path, headers={"Authorization": f"Bearer {self.token(**claims)}"}
        )

    def assert_no_history_read(self):
        self.chat.scalars.assert_not_awaited()

    def test_scoped_visitor_and_dashboard_staff_can_read_history(self):
        for caller_type in ("user", "support_agent"):
            with self.subTest(caller_type=caller_type):
                response = self.get(type=caller_type)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.json()["messages"][0]["content"],
                    "Synthetic private transcript",
                )

    def test_other_conversation_is_denied_even_under_same_organization_and_bot(self):
        for caller_type in ("user", "support_agent"):
            with self.subTest(caller_type=caller_type):
                response = self.get(type=caller_type, conversation_id=str(uuid4()))
                self.assertEqual(response.status_code, 403)
        self.factory.assert_not_called()
        self.assert_no_history_read()

    def test_missing_or_invalid_conversation_scope_is_denied(self):
        for scope in (None, "", "invalid", 42, {}):
            with self.subTest(scope=scope):
                self.assertEqual(self.get(conversation_id=scope).status_code, 403)
        self.factory.assert_not_called()
        self.assert_no_history_read()

    def test_missing_or_unsupported_token_type_is_denied(self):
        for caller_type in (None, "", "sse", "access", "training", [], {}):
            with self.subTest(caller_type=caller_type):
                self.assertEqual(self.get(type=caller_type).status_code, 403)
        self.factory.assert_not_called()
        self.assert_no_history_read()

    def test_bot_wide_training_token_is_denied(self):
        self.assertEqual(
            self.get(type="support_agent", conversation_id=None).status_code, 403
        )
        self.factory.assert_not_called()
        self.assert_no_history_read()

    def test_missing_or_invalid_bot_claim_is_denied(self):
        for bot in (None, "invalid", 42, {}):
            with self.subTest(bot=bot):
                self.assertEqual(self.get(bot_id=bot).status_code, 403)
        self.factory.assert_not_called()
        self.assert_no_history_read()

    def test_other_organization_is_not_found(self):
        self.assertEqual(self.get(organization_id="other-org").status_code, 404)
        self.dashboard.scalar.assert_awaited_once()
        self.assert_no_history_read()

    def test_other_bot_is_not_found(self):
        self.assertEqual(self.get(bot_id=str(uuid4())).status_code, 404)
        self.dashboard.scalar.assert_awaited_once()
        self.assert_no_history_read()

    def test_missing_conversation_is_not_found(self):
        self.connection.execute(self.conversations.delete())
        self.assertEqual(self.get().status_code, 404)
        self.assert_no_history_read()

    def test_missing_or_invalid_authentication_is_denied(self):
        headers = (
            {},
            {"Authorization": "Bearer invalid"},
            {"Authorization": f"Bearer {self.token()}invalid"},
        )
        for value in headers:
            with self.subTest(headers_present=bool(value)):
                self.assertEqual(
                    self.client.get(self.path, headers=value).status_code, 401
                )
        self.factory.assert_not_called()
        self.assert_no_history_read()

    def test_expired_wrong_issuer_and_wrong_audience_are_denied(self):
        for claims in (
            {"exp": int(time.time()) - 60},
            {"iss": "other-server"},
            {"aud": "other-service"},
        ):
            with self.subTest(claims=claims):
                self.assertEqual(self.get(**claims).status_code, 401)
        self.factory.assert_not_called()
        self.assert_no_history_read()

    def test_malformed_conversation_path_is_a_validation_error(self):
        response = self.client.get(
            "/api/conversations/not-a-uuid/messages",
            headers={"Authorization": f"Bearer {self.token()}"},
        )
        self.assertEqual(response.status_code, 422)
        self.factory.assert_not_called()
        self.assert_no_history_read()

    def test_ownership_lookup_failure_does_not_expose_history(self):
        self.dashboard.scalar.side_effect = RuntimeError("Synthetic DB failure")
        with self.assertLogs("app.api.routes.messages", level="ERROR"):
            response = self.get()
        self.assertEqual(response.status_code, 500)
        self.assert_no_history_read()


if __name__ == "__main__":
    unittest.main()
