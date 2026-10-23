import asyncio
import json
import logging
import os
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.methods import DeleteMessage
from aiogram.types import Update

from audit import DeletionJournal
from main import create_dispatcher, delete_with_journal


def records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


async def test_confirmed_deletion_keeps_full_message(message_factory, telegram, tmp_path):
    bot, session = telegram
    journal = DeletionJournal(str(tmp_path / "messages.jsonl"))
    message = message_factory(text="Ссылка https://example.com").as_(bot)
    assert await delete_with_journal(message, logging.getLogger("test"), "link", journal)
    before, after = records(journal.path)
    assert [before["event"], after["event"]] == ["delete_requested", "deleted"]
    assert before["operation_id"] == after["operation_id"]
    assert after["message"]["text"] == message.text
    assert after["message"]["from"]["id"] == 42
    assert after["chat_id"] == message.chat.id
    assert after["reason"] == "link"
    assert len(session.deletions) == 1


async def test_media_caption_and_file_id_are_saved(message_factory, telegram, tmp_path):
    bot, _ = telegram
    journal = DeletionJournal(str(tmp_path / "messages.jsonl"))
    message = message_factory(
        text=None,
        caption="Рекламный файл",
        document={"file_id": "file", "file_unique_id": "unique"},
    ).as_(bot)
    assert await delete_with_journal(message, logging.getLogger("test"), "media", journal)
    record = records(journal.path)[-1]
    assert record["message"]["caption"] == message.caption
    assert record["message"]["document"]["file_id"] == "file"


@pytest.mark.parametrize(
    "error_type,detail",
    [
        (TelegramForbiddenError, "not enough rights"),
        (TelegramBadRequest, "message to delete not found"),
    ],
)
async def test_failed_deletion_is_not_marked_deleted(
    message_factory, telegram, tmp_path, error_type, detail
):
    bot, session = telegram
    session.delete_errors = [
        error_type(method=DeleteMessage(chat_id=1, message_id=1), message=detail)
    ]
    journal = DeletionJournal(str(tmp_path / "messages.jsonl"))
    assert not await delete_with_journal(
        message_factory().as_(bot), logging.getLogger("test"), "test", journal
    )
    assert [row["event"] for row in records(journal.path)] == ["delete_requested", "not_deleted"]


async def test_unwritable_archive_prevents_unrecorded_deletion(
    message_factory, telegram, tmp_path, monkeypatch
):
    bot, session = telegram
    journal = DeletionJournal(str(tmp_path / "messages.jsonl"))
    monkeypatch.setattr(journal, "begin", AsyncMock(side_effect=OSError("disk full")))
    assert not await delete_with_journal(
        message_factory().as_(bot), logging.getLogger("test"), "test", journal
    )
    assert not session.deletions


async def test_result_write_failure_retains_original(
    message_factory, telegram, tmp_path, monkeypatch
):
    bot, _ = telegram
    journal = DeletionJournal(str(tmp_path / "messages.jsonl"))
    original = journal.record

    async def fail_confirmation(event, *args):
        if event == "deleted":
            raise OSError("disk full")
        await original(event, *args)

    monkeypatch.setattr(journal, "record", fail_confirmation)
    assert await delete_with_journal(
        message_factory().as_(bot), logging.getLogger("test"), "test", journal
    )
    assert records(journal.path)[0]["message"]["text"] == message_factory().text


async def test_concurrent_writes_and_restarts_keep_records(message_factory, tmp_path):
    path = tmp_path / "messages.jsonl"
    journal = DeletionJournal(str(path))
    await asyncio.gather(
        *[journal.begin(message_factory(message_id=index), "test") for index in range(20)]
    )
    await DeletionJournal(str(path)).begin(message_factory(message_id=20), "test")
    assert sorted(row["message_id"] for row in records(path)) == list(range(21))
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("status", ["member", "administrator", "creator"])
async def test_dispatcher_journals_only_moderated_members(message_factory, telegram, cfg, status):
    bot, session = telegram
    session.member_status = status
    await create_dispatcher(cfg).feed_update(
        bot, Update(update_id=1, message=message_factory(text="https://example.com"))
    )
    from pathlib import Path

    saved = records(Path(cfg.deletion_log_path))
    assert len(saved) == (2 if status == "member" else 0)
