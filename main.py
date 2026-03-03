import asyncio
import logging
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import Message

from config import load_config
from moderation import decide


LOG_FILE = Path("moderation_log.txt")


def write_moderation_log(message: Message, reason: str, extra: str = "") -> None:
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        text = (message.text or message.caption or "").replace("\n", " ").strip()
        if len(text) > 500:
            text = text[:500] + "..."

        line = (
            f"[{ts}] "
            f"chat_id={message.chat.id} "
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
        log.warning("DELETE BAD REQUEST | %s", e)
        return False
    except TelegramForbiddenError as e:
        log.error("DELETE FORBIDDEN (no rights) | %s", e)
        return False
    except Exception as e:
        log.exception("DELETE UNKNOWN ERROR | %r", e)
        return False


def _detect_forbidden_media_kind(message: Message) -> str | None:
    if message.video:
        return "video"
    if message.photo:
        return "photo"
    if message.document:
        return "document"
    return None


async def main() -> None:
    cfg = load_config()

    logging.basicConfig(
        level=getattr(logging, cfg.log_level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    log = logging.getLogger("antiad")

    # === Конфигурация при старте ===
    log.info("=== BOT START ===")
    log.info("CONFIG | test_mode_delete_admins=%s", cfg.test_mode_delete_admins)
    log.info("CONFIG | target_chat_id=%s", cfg.target_chat_id)
    log.info("CONFIG | delete_channel_messages=%s", cfg.delete_channel_messages)
    log.info("CONFIG | ad_score_threshold=%s", cfg.ad_score_threshold)

    bot = Bot(token=cfg.bot_token)
    dp = Dispatcher()

    @dp.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
    async def handle_group_message(message: Message) -> None:
        log.info(
            "MESSAGE | chat=%s | user=%s | username=%s | text=%r",
            message.chat.id,
            getattr(message.from_user, "id", None),
            getattr(message.from_user, "username", None),
            message.text or message.caption,
        )

        # Проверка чата
        if cfg.target_chat_id is not None and message.chat.id != cfg.target_chat_id:
            log.info("SKIP | wrong chat_id")
            return

        # Проверка статуса пользователя
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

        # Медиа
        forbidden_kind = _detect_forbidden_media_kind(message)
        if forbidden_kind:
            log.info("TRIGGER | forbidden media | type=%s", forbidden_kind)
            write_moderation_log(message, "media_forbidden", f"type={forbidden_kind}")
            await safe_delete(message, log, f"media_forbidden:{forbidden_kind}")
            return

        # Channel sender
        if cfg.delete_channel_messages and message.sender_chat is not None:
            if message.sender_chat.type == ChatType.CHANNEL:
                log.info("TRIGGER | channel sender message")
                write_moderation_log(message, "channel_sender")
                await safe_delete(message, log, "channel_sender")
                return

        # Антиреклама
        d = decide(message, threshold=cfg.ad_score_threshold)

        log.info(
            "DECIDE | score=%s | should_delete=%s | reasons=%s",
            d.score,
            d.should_delete,
            d.reasons,
        )

        if d.should_delete:
            log.info("TRIGGER | ad detected")
            write_moderation_log(
                message,
                "ad_detected",
                f"score={d.score} reasons={','.join(d.reasons)}",
            )
            await safe_delete(message, log, f"ad_score:{d.score}")
            return

        log.info("MESSAGE PASSED")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())