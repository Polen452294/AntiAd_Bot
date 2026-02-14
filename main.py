import asyncio
import logging
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from config import load_config
from moderation import decide


LOG_FILE = Path("moderation_log.txt")


def write_moderation_log(message: Message, reason: str, extra: str = "") -> None:
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        chat_id = message.chat.id
        msg_id = message.message_id
        media_group_id = getattr(message, "media_group_id", None)

        user_id = getattr(message.from_user, "id", None)
        username = getattr(message.from_user, "username", None)

        sender_chat_id = getattr(message.sender_chat, "id", None) if message.sender_chat else None
        sender_chat_type = getattr(message.sender_chat, "type", None) if message.sender_chat else None

        text = (message.text or message.caption or "").replace("\n", " ").strip()
        if len(text) > 500:
            text = text[:500] + "..."

        line = (
            f"[{ts}] "
            f"chat_id={chat_id} msg_id={msg_id} media_group_id={media_group_id} "
            f"user_id={user_id} username={username} "
            f"sender_chat_id={sender_chat_id} sender_chat_type={sender_chat_type} "
            f"reason={reason} {extra} text={text!r}\n"
        )

        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


async def safe_delete(message: Message, log: logging.Logger, reason: str) -> bool:
    try:
        await message.delete()
        log.info("Deleted message. chat_id=%s message_id=%s reason=%s", message.chat.id, message.message_id, reason)
        return True
    except TelegramForbiddenError as e:
        log.warning("NO RIGHTS TO DELETE. chat_id=%s message_id=%s reason=%s err=%s",
                    message.chat.id, message.message_id, reason, e)
        return False
    except TelegramBadRequest as e:
        log.warning("DELETE FAILED (BadRequest). chat_id=%s msg_id=%s reason=%s err=%s",
                    message.chat.id, message.message_id, reason, e)
        log.warning("DELETE DEBUG: content_type=%s from_user=%s sender_chat=%s",
                    message.content_type,
                    getattr(message.from_user, "id", None),
                    getattr(message.sender_chat, "id", None) if message.sender_chat else None)
        return False
    except Exception as e:
        log.exception("UNEXPECTED DELETE ERROR. chat_id=%s message_id=%s reason=%s err=%r",
                      message.chat.id, message.message_id, reason, e)
        return False


def _detect_forbidden_media_kind(message: Message) -> str | None:
    ct = message.content_type
    if ct == "photo":
        return "photo"
    if ct == "video":
        return "video"
    if ct == "document" and message.document:
        mt = (message.document.mime_type or "").lower()
        if mt.startswith("image/"):
            return "image_document"
        return "document"
    return None


async def _is_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False


async def check_bot_can_moderate(bot: Bot, log: logging.Logger, chat_id: int) -> bool:
    """
    Возвращает True, если бот является админом в чате и у него есть can_delete_messages.
    Если нет — пишет явное предупреждение.
    """
    try:
        me = await bot.get_me()
        cm = await bot.get_chat_member(chat_id, me.id)

        status = getattr(cm, "status", None)
        can_delete = getattr(cm, "can_delete_messages", None)

        log.warning(
            "BOT PERMISSIONS CHECK: chat_id=%s bot_id=%s status=%s can_delete_messages=%s",
            chat_id, me.id, status, can_delete
        )

        if status not in ("administrator", "creator") or can_delete is not True:
            log.error(
                "MODERATION DISABLED: bot is not admin or has no delete rights in chat_id=%s. "
                "Make bot admin in the discussion group and enable 'Delete messages'.",
                chat_id
            )
            return False

        return True

    except Exception as e:
        log.error("MODERATION DISABLED: failed to check bot permissions in chat_id=%s err=%r", chat_id, e)
        return False


async def main() -> None:
    cfg = load_config()

    logging.basicConfig(
        level=getattr(logging, cfg.log_level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    log = logging.getLogger("antiad")

    bot = Bot(token=cfg.bot_token)
    dp = Dispatcher()

    # Флаг: можно ли модерировать (есть ли права у бота)
    moderation_enabled = True
    if cfg.target_chat_id is not None:
        moderation_enabled = await check_bot_can_moderate(bot, log, cfg.target_chat_id)

    @dp.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
    async def handle_group_message(message: Message) -> None:
        if cfg.target_chat_id is not None and message.chat.id != cfg.target_chat_id:
            return

        # Если прав нет — ничего не пытаемся удалять (чтобы не было "тихих" ошибок)
        if not moderation_enabled:
            return

        # 1) Запрет медиа/файлов всем, кроме админов
        forbidden_kind = _detect_forbidden_media_kind(message)
        if forbidden_kind:
            write_moderation_log(message, "media_detected", extra=f"type={forbidden_kind}")

        if forbidden_kind and message.from_user:
            if not await _is_admin(bot, message.chat.id, message.from_user.id):
                write_moderation_log(message, "media_non_admin", extra=f"type={forbidden_kind}")
                await safe_delete(message, log, f"media_non_admin:{forbidden_kind}")
                return

        # 2) Запрет сообщений от лица других каналов
        if cfg.delete_channel_messages and message.sender_chat:
            if message.sender_chat.type == ChatType.CHANNEL:
                write_moderation_log(message, "channel_sender")
                await safe_delete(message, log, "channel_sender")
                return

        # 3) Антиреклама
        d = decide(message, threshold=cfg.ad_score_threshold)
        if d.should_delete:
            extra = f"score={d.score} reasons={','.join(d.reasons)}"
            write_moderation_log(message, "ad_detected", extra=extra)
            await safe_delete(message, log, f"ad_score:{d.score}")
            return

    log.info(
        "Bot started. target_chat_id=%s delete_channel_messages=%s ad_score_threshold=%s moderation_enabled=%s",
        cfg.target_chat_id,
        cfg.delete_channel_messages,
        cfg.ad_score_threshold,
        moderation_enabled,
    )
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())