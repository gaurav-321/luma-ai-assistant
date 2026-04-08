from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class WorkItem:
    query: str
    username: str
    chat_id: int
    thread_id: int | None = None
    reply_to_message_id: int | None = None
    source: str = "telegram"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ReplyItem:
    chat_id: int
    text: str
    thread_id: int | None = None
    reply_to_message_id: int | None = None
    source: str = "agent"
    metadata: dict[str, Any] = field(default_factory=dict)


msg_queue: asyncio.Queue[WorkItem] = asyncio.Queue()
reply_queue: asyncio.Queue[ReplyItem] = asyncio.Queue()
