"""SNS child targets stay beneath the exact recorded topic ARN."""

from __future__ import annotations

import re
from typing import Any

from aws.managed._base import ManagedServiceError


def assert_subscription_parent(value: Any, topic_arn: str) -> None:
    if isinstance(value, str) and value.lower() in {"pendingconfirmation", "deleted"}:
        return
    prefix = topic_arn + ":"
    suffix = value[len(prefix) :] if isinstance(value, str) and value.startswith(prefix) else ""
    if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", suffix) is None:
        raise ManagedServiceError("SNS subscription does not belong to the exact topic")


def assert_listed_subscription_parent(item: Any, topic_arn: str) -> None:
    if not isinstance(item, dict) or item.get("TopicArn") != topic_arn:
        raise ManagedServiceError("listed SNS subscription parent does not match the exact topic")
    assert_subscription_parent(item.get("SubscriptionArn"), topic_arn)
