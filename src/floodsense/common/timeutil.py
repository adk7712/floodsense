"""Timezone helpers. FloodSense works in Singapore time end to end."""

from datetime import datetime
from typing import TypeVar

from floodsense.common.config import settings

T = TypeVar("T", bound=datetime)


def to_sgt(ts: T) -> T:
    """Return ``ts`` in Singapore time. Naive timestamps are assumed to already be SGT."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=settings.tzinfo)
    return ts.astimezone(settings.tzinfo)
