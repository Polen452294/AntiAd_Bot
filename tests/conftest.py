from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import DeleteMessage, GetChat, GetChatMember
from aiogram.types import Message


@pytest.fixture
def message_factory():
    def make_message(**overrides):
        data = {
            "message_id": 1,
            "date": datetime.now(timezone.utc),
            "chat": {"id": -1001234567890, "type": "supergroup"},
            "from": {"id": 42, "is_bot": False, "first_name": "User"},
            "text": "Привет всем!",
        }
        data.update(overrides)
        return Message.model_validate(data)

    return make_message


class FakeTelegramSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.member_status = "member"
        self.member_error = None
        self.delete_errors = []
        self.chat_type = "supergroup"
        self.bot_can_delete = True

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, GetChatMember):
            if self.member_error:
                raise self.member_error
            return SimpleNamespace(
                status="administrator" if method.user_id == bot.id else self.member_status,
                can_delete_messages=self.bot_can_delete,
            )
        if isinstance(method, GetChat):
            return SimpleNamespace(id=method.chat_id, type=self.chat_type)
        if isinstance(method, DeleteMessage):
            if self.delete_errors:
                raise self.delete_errors.pop(0)
            return True
        raise AssertionError(f"Unexpected Telegram call: {method.__class__.__name__}")

    async def stream_content(self, *args, **kwargs):
        raise AssertionError("Downloading media is not needed for moderation")
        yield b""  # Make this an async generator, as required by BaseSession.

    @property
    def deletions(self):
        return [call for call in self.calls if isinstance(call, DeleteMessage)]


@pytest.fixture
def telegram():
    session = FakeTelegramSession()
    bot = Bot(token="123456:TEST_TOKEN_FOR_OFFLINE_TESTS", session=session)
    return bot, session


@pytest.fixture
def cfg():
    from config import Config

    return Config(
        bot_token="123456:TEST_TOKEN_FOR_OFFLINE_TESTS",
        target_chat_id=None,
        delete_channel_messages=True,
        ad_score_threshold=2,
        log_level="INFO",
    )
