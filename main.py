import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import Message

from config import load_config


LOG_FILE = Path("moderation_log.txt")

LINK_RE = re.compile(
    r"("
    r"https?://\S+"
    r"|www\.\S+"
    r"|t\.me/\S+"
    r"|telegram\.me/\S+"
    r"|vk\.com/\S+"
    r"|vkontakte\.ru/\S+"
    r"|discord\.gg/\S+"
    r"|wa\.me/\S+"
    r"|bit\.ly/\S+"
    r"|cutt\.ly/\S+"
    r"|tinyurl\.com/\S+"
    r"|goo\.su/\S+"
    r")",
    re.IGNORECASE,
)

USERNAME_RE = re.compile(
    r"(?<!\w)@[A-Za-z0-9_]{4,32}\b",
    re.IGNORECASE,
)


def write_moderation_log(message: Message, reason: str, extra: str = "") -> None:
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        text = (message.text or message.caption or "").replace("\n", " ").strip()
        if len(text) > 500:
            text = text[:500] + "..."

        line = (
            f"[{ts}] "
            f"chat_id={message.chat.id} "
            f"msg_id={message.message_id} "
            f"user_id={getattr(message.from_user, 'id', None)} "
            f"username={getattr(message.from_user, 'username', None)} "
            f"reason={reason} "
            f"{extra} "
            f"text={text!r}\n"
        )

        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


async def safe_delete(message: Message, log: logging.Logger, reason: str) -> bool:
    try:
        await message.delete()
        log.info(
            "DELETE SUCCESS | chat_id=%s | msg_id=%s | reason=%s",
            message.chat.id,
            message.message_id,
            reason,
        )
        return True
    except TelegramBadRequest as e:
        log.warning(
            "DELETE BAD REQUEST | chat_id=%s | msg_id=%s | reason=%s | err=%s",
            message.chat.id,
            message.message_id,
            reason,
            e,
        )
        return False
    except TelegramForbiddenError as e:
        log.error(
            "DELETE FORBIDDEN | chat_id=%s | msg_id=%s | reason=%s | err=%s",
            message.chat.id,
            message.message_id,
            reason,
            e,
        )
        return False
    except Exception as e:
        log.exception(
            "DELETE UNKNOWN ERROR | chat_id=%s | msg_id=%s | reason=%s | err=%r",
            message.chat.id,
            message.message_id,
            reason,
            e,
        )
        return False


def _extract_text(message: Message) -> str:
    return (message.text or message.caption or "").strip()


def _detect_forbidden_media_kind(message: Message) -> str | None:
    if message.photo:
        return "photo"
    if message.video:
        return "video"
    if message.document:
        return "document"
    return None


def _detect_link_reason(message: Message) -> str | None:
    text = _extract_text(message)
    if not text:
        return None

    if LINK_RE.search(text):
        return "link"
    if USERNAME_RE.search(text):
        return "username"
    return None


async def main() -> None:
    cfg = load_config()

    logging.basicConfig(
        level=getattr(logging, cfg.log_level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    log = logging.getLogger("antiad")

    log.info("=== BOT START ===")
    log.info("CONFIG | test_mode_delete_admins=%s", cfg.test_mode_delete_admins)
    log.info("CONFIG | target_chat_id=%s", cfg.target_chat_id)
    log.info("CONFIG | delete_channel_messages=%s", cfg.delete_channel_messages)

    bot = Bot(token=cfg.bot_token)
    dp = Dispatcher()

    @dp.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
    async def handle_group_message(message: Message) -> None:
        log.info(
            "MESSAGE | chat=%s | user=%s | username=%s | text=%r",
            message.chat.id,
            getattr(message.from_user, "id", None),
            getattr(message.from_user, "username", None),
            _extract_text(message),
        )

        if cfg.target_chat_id is not None and message.chat.id != cfg.target_chat_id:
            log.info("SKIP | wrong chat_id")
            return

        if message.from_user:
            try:
                member = await bot.get_chat_member(message.chat.id, message.from_user.id)
                log.info(
                    "USER STATUS | user_id=%s | status=%s | test_mode=%s",
                    message.from_user.id,
                    member.status,
                    cfg.test_mode_delete_admins,
                )

                if (
                    not cfg.test_mode_delete_admins
                    and member.status in ("administrator", "creator")
                ):
                    log.info("SKIP | admin and test_mode disabled")
                    return

            except Exception as e:
                log.exception("ERROR getting member status | %r", e)

        forbidden_kind = _detect_forbidden_media_kind(message)
        if forbidden_kind:
            log.info("TRIGGER | forbidden media | type=%s", forbidden_kind)
            write_moderation_log(message, "media_forbidden", f"type={forbidden_kind}")
            await safe_delete(message, log, f"media_forbidden:{forbidden_kind}")
            return

        if cfg.delete_channel_messages and message.sender_chat is not None:
            if message.sender_chat.type == ChatType.CHANNEL:
                log.info("TRIGGER | channel sender message")
                write_moderation_log(message, "channel_sender")
                await safe_delete(message, log, "channel_sender")
                return

        link_reason = _detect_link_reason(message)
        if link_reason:
            log.info("TRIGGER | %s detected", link_reason)
            write_moderation_log(message, "link_detected", f"type={link_reason}")
            await safe_delete(message, log, f"link_detected:{link_reason}")
            return

        log.info("MESSAGE PASSED")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())