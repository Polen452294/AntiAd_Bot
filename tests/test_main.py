import logging
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.methods import DeleteMessage, GetChatMember
from aiogram.types import Update

from main import check_target_chat, create_dispatcher, safe_delete


@pytest.mark.parametrize("edited", [False, True])
@pytest.mark.parametrize("status", ["member", "restricted", "administrator", "creator"])
@pytest.mark.parametrize(
    "content",
    [
        {"text": "example.com"},
        {"text": None, "document": {"file_id": "x", "file_unique_id": "x"}},
        {"text": "Подписывайтесь на наш канал!"},
    ],
)
async def test_moderation_routes_exempt_administrators(
    message_factory, telegram, cfg, edited, status, content
):
    bot, session = telegram
    session.member_status = status
    message = message_factory(**content)
    update = Update(update_id=1, **{"edited_message" if edited else "message": message})
    await create_dispatcher(cfg).feed_update(bot, update)
    assert len(session.deletions) == (0 if status in {"administrator", "creator"} else 1)
    if session.deletions:
        assert session.deletions[0].chat_id == message.chat.id
        assert session.deletions[0].message_id == message.message_id


@pytest.mark.parametrize("automatic", [False, True])
async def test_anonymous_admins_and_automatic_channel_posts_are_kept(
    message_factory, telegram, cfg, automatic
):
    bot, session = telegram
    message = message_factory(
        text=None,
        photo=[{"file_id": "x", "file_unique_id": "x", "width": 1, "height": 1}],
        sender_chat={
            "id": -100777 if automatic else -1001234567890,
            "type": "channel" if automatic else "supergroup",
        },
        is_automatic_forward=automatic,
    )
    await create_dispatcher(cfg).feed_update(bot, Update(update_id=1, message=message))
    assert not session.calls


@pytest.mark.parametrize("channel_switch", [False, True])
async def test_foreign_channel_cannot_bypass_media_filter(
    message_factory, telegram, cfg, channel_switch
):
    bot, session = telegram
    message = message_factory(
        text=None,
        document={"file_id": "x", "file_unique_id": "x"},
        sender_chat={"id": -100777, "type": "channel"},
    )
    await create_dispatcher(replace(cfg, delete_channel_messages=channel_switch)).feed_update(
        bot, Update(update_id=1, message=message)
    )
    assert len(session.deletions) == 1
    assert not any(isinstance(call, GetChatMember) for call in session.calls)


async def test_admin_lookup_failure_does_not_delete(message_factory, telegram, cfg, caplog):
    bot, session = telegram
    session.member_error = TelegramBadRequest(
        method=GetChatMember(chat_id=1, user_id=42), message="not enough rights"
    )
    message = message_factory(text="https://example.com")
    await create_dispatcher(cfg).feed_update(bot, Update(update_id=1, message=message))
    assert not session.deletions
    assert "ADMIN CHECK FAILED" in caplog.text
    assert message.text not in caplog.text


@pytest.mark.parametrize(
    "chat",
    [
        {"id": 42, "type": "private"},
        {"id": -10099999, "type": "supergroup"},
        {"id": -10088888, "type": "channel"},
    ],
)
async def test_messages_outside_target_group_are_ignored(message_factory, telegram, cfg, chat):
    bot, session = telegram
    cfg = replace(cfg, target_chat_id=-1001234567890)
    message = message_factory(text="example.com", chat=chat)
    await create_dispatcher(cfg).feed_update(bot, Update(update_id=1, message=message))
    assert not session.calls


async def test_repeat_spam_is_deleted_by_dispatcher(message_factory, telegram, cfg):
    bot, session = telegram
    dispatcher = create_dispatcher(cfg)
    for index in range(1, 4):
        await dispatcher.feed_update(
            bot, Update(update_id=index, message=message_factory(message_id=index))
        )
    assert [call.message_id for call in session.deletions] == [3]


async def test_admin_promotion_takes_effect_immediately(message_factory, telegram, cfg):
    bot, session = telegram
    dispatcher = create_dispatcher(cfg)
    for index in range(1, 3):
        await dispatcher.feed_update(
            bot, Update(update_id=index, message=message_factory(message_id=index))
        )
    session.member_status = "administrator"
    await dispatcher.feed_update(bot, Update(update_id=3, message=message_factory(message_id=3)))
    assert not session.deletions


@pytest.mark.parametrize("error_type", [TelegramBadRequest, TelegramForbiddenError])
async def test_deletion_errors_are_logged(message_factory, telegram, error_type, caplog):
    bot, session = telegram
    session.delete_errors = [
        error_type(method=DeleteMessage(chat_id=1, message_id=1), message="not enough rights")
    ]
    assert not await safe_delete(message_factory().as_(bot), logging.getLogger("test"), "test")
    assert len(session.deletions) == 1
    assert "DELETE" in caplog.text


@pytest.mark.parametrize("error_type", [TelegramNetworkError, TelegramRetryAfter])
async def test_transient_deletion_failure_is_retried(
    message_factory, telegram, error_type, monkeypatch
):
    bot, session = telegram
    kwargs = {"retry_after": 1} if error_type is TelegramRetryAfter else {}
    session.delete_errors = [
        error_type(method=DeleteMessage(chat_id=1, message_id=1), message="retry", **kwargs)
    ]
    sleep = AsyncMock()
    monkeypatch.setattr("main.asyncio.sleep", sleep)
    assert await safe_delete(message_factory().as_(bot), logging.getLogger("test"), "test")
    assert len(session.deletions) == 2
    sleep.assert_awaited_once()


async def test_deletion_retries_are_bounded(message_factory, telegram, monkeypatch):
    bot, session = telegram
    session.delete_errors = [
        TelegramNetworkError(method=DeleteMessage(chat_id=1, message_id=1), message="network")
        for _ in range(3)
    ]
    monkeypatch.setattr("main.asyncio.sleep", AsyncMock())
    assert not await safe_delete(message_factory().as_(bot), logging.getLogger("test"), "test")
    assert len(session.deletions) == 3


async def test_startup_fails_if_bot_lacks_delete_permission(telegram, cfg):
    bot, session = telegram
    session.bot_can_delete = False
    with pytest.raises(RuntimeError, match="администратором"):
        await check_target_chat(bot, replace(cfg, target_chat_id=-1001234567890))


async def test_startup_rejects_channel_id(telegram, cfg):
    bot, session = telegram
    session.chat_type = "channel"
    with pytest.raises(RuntimeError, match="группу обсуждений"):
        await check_target_chat(bot, replace(cfg, target_chat_id=-1001234567890))


def test_required_update_types_are_registered(cfg):
    assert set(create_dispatcher(cfg).resolve_used_update_types()) == {
        "message",
        "edited_message",
        "my_chat_member",
    }
