"""Append-only deletion journal with a durable copy before destructive API calls."""

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4

from aiogram.types import Message


class DeletionJournal:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = Lock()
        # Validate write access before polling begins. Never truncate previous records.
        self._append_bytes(b"")

    def _append_bytes(self, content: bytes) -> None:
        with self._lock:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(descriptor, "ab") as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())

    async def record(self, event: str, message: Message, reason: str, operation_id: str) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "operation_id": operation_id,
            "chat_id": message.chat.id,
            "message_id": message.message_id,
            "reason": reason,
            "message": message.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
                exclude={"reply_to_message", "external_reply", "pinned_message"},
            ),
        }
        content = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        await asyncio.to_thread(self._append_bytes, content)

    async def begin(self, message: Message, reason: str) -> str:
        operation_id = uuid4().hex
        await self.record("delete_requested", message, reason, operation_id)
        return operation_id
