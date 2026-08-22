from __future__ import annotations

import asyncio
import datetime
import json
import logging
import uuid
from typing import Any

from sqlalchemy import select

from app.db.session import create_dashboard_db_session
from app.infra.redis_client import redis_client
from app.models.dashboard_db_models import (
    Notifications,
    OrganizationMembers,
)

logger = logging.getLogger(__name__)

NotificationPayload = dict[str, Any]


def _create_notification_rows_sync(
    *,
    organization_id: str,
    notification_type: str,
    title: str,
    body: str,
    metadata: dict[str, Any],
) -> list[NotificationPayload]:
    now = datetime.datetime.now(datetime.UTC)

    with create_dashboard_db_session() as session:
        recipient_ids = session.scalars(
            select(OrganizationMembers.user_id).where(
                OrganizationMembers.organization_id == organization_id,
                OrganizationMembers.user_id.is_not(None),
            )
        ).all()

        # Prevent duplicate rows if duplicate memberships exist.
        unique_recipient_ids = list(dict.fromkeys(recipient_ids))

        if not unique_recipient_ids:
            logger.warning(
                "No recipients found for dashboard notification",
                extra={"organization_id": organization_id},
            )
            return []

        rows: list[Notifications] = []
        payloads: list[NotificationPayload] = []

        for user_id in unique_recipient_ids:
            if user_id is None:
                continue

            notification_id = uuid.uuid4()

            rows.append(
                Notifications(
                    id=notification_id,
                    organization_id=organization_id,
                    user_id=user_id,
                    title=title,
                    body=body,
                    type=notification_type,
                    read_at=None,
                    created_at=now,
                    metadata_json=metadata.copy(),
                    channels=["dashboard"],
                )
            )

            payloads.append(
                {
                    "id": str(notification_id),
                    "user_id": str(user_id),
                    "type": notification_type,
                    "title": title,
                    "body": body,
                    "createdAt": now.isoformat(),
                    "readAt": None,
                    "metadata": metadata.copy(),
                }
            )

        session.add_all(rows)
        session.commit()

        return payloads


async def create_notifications(
    *,
    organization_id: str,
    notification_type: str,
    title: str,
    body: str,
    metadata: dict[str, Any],
) -> list[NotificationPayload]:
    return await asyncio.to_thread(
        _create_notification_rows_sync,
        organization_id=organization_id,
        notification_type=notification_type,
        title=title,
        body=body,
        metadata=metadata,
    )


def _publish_notifications_sync(
    *,
    organization_id: str,
    notifications: list[NotificationPayload],
) -> None:
    channel = f"org_notifications:{organization_id}"

    for notification in notifications:
        try:
            subscriber_count = redis_client.publish(
                channel,
                json.dumps(notification),
            )

            logger.info(
                "Dashboard notification published",
                extra={
                    "notification_id": notification["id"],
                    "user_id": notification["user_id"],
                    "subscriber_count": subscriber_count,
                },
            )
        except Exception:
            # The database row already exists. Dashboard reload can
            # recover it even if real-time delivery fails.
            logger.exception(
                "Dashboard notification publication failed",
                extra={
                    "notification_id": notification.get("id"),
                    "user_id": notification.get("user_id"),
                    "channel": channel,
                },
            )


async def publish_notifications(
    *,
    organization_id: str,
    notifications: list[NotificationPayload],
) -> None:
    if not notifications:
        return

    await asyncio.to_thread(
        _publish_notifications_sync,
        organization_id=organization_id,
        notifications=notifications,
    )
