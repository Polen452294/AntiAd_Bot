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
    """
    Аудит модерации в текстовый файл. Никогда не ломает бота.
    """
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        chat_id = message.chat.id
        msg_id = message.message_id
        media_group_id = getattr(message, "media_group_id", None)

        user_id = getattr(message.from_user, "id", None)
        username = getattr(message.from_user, "username", None)

        sender_chat_id = getattr(message.sender_chat, "id", None) if message.sender_chat else None
        sender_chat_type = getattr(message.sender_chat, "type", None) if message.sender_chat else None
        sender_chat_title = getattr(message.sender_chat, "title", None) if message.sender_chat else None

        text = (message.text or message.caption or "").replace("\n", " ").strip()
        if len(text) > 500:
            text = text[:500] + "..."

        line = (
            f"[{ts}] "
            f"chat_id={chat_id} msg_id={msg_id} media_group_id={media_group_id} "
            f"user_id={user_id} username={username} "
            f"sender_chat_id={sender_chat_id} sender_chat_type={sender_chat_type} sender_chat_title={sender_chat_title!r} "
            f"reason={reason} "
            f"{extra} "
            f"text={text!r}\n"
        )

        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


async def safe_delete(message: Message, log: logging.Logger, reason: str) -> bool:
    """
    Пытаемся удалить сообщение, но никогда не падаем, если Telegram не разрешил.
    При неуспехе пишем в moderation_log.txt запись delete_failed.
    """
    try:
        await message.delete()
        log.info("Deleted message. chat_id=%s message_id=%s reason=%s", message.chat.id, message.message_id, reason)
        return True
    except TelegramForbiddenError as e:
        log.warning(
            "No rights to delete. chat_id=%s message_id=%s reason=%s err=%s",
            message.chat.id,
            message.message_id,
            reason,
            e,
        )
        write_moderation_log(message, "delete_failed", extra=f"reason={reason} err={str(e)!r}")
        return False
    except TelegramBadRequest as e:
        log.info(
            "Skip delete (bad request). chat_id=%s message_id=%s reason=%s err=%s",
            message.chat.id,
            message.message_id,
            reason,
            e,
        )
        write_moderation_log(message, "delete_failed", extra=f"reason={reason} err={str(e)!r}")
        return False
    except Exception as e:
        log.exception(
            "Unexpected delete error. chat_id=%s message_id=%s reason=%s err=%r",
            message.chat.id,
            message.message_id,
            reason,
            e,
        )
        write_moderation_log(message, "delete_failed", extra=f"reason={reason} err={repr(e)}")
        return False


def _detect_forbidden_media_kind(message: Message) -> str | None:
    """
    Запрещаем для НЕ-админов:
    - фото (Telegram-сжатие) -> content_type == photo
    - видео -> content_type == video
    - любые файлы -> content_type == document
      + отдельно помечаем картинки, отправленные как файл (mime_type image/*)
    """
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
    """
    Fail-closed: если Telegram не дал проверить статус — считаем пользователя НЕ админом.
    Это нужно, чтобы медиа не обходили из-за временных ошибок API.
    """
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except Exception:
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

    @dp.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
    async def handle_group_message(message: Message) -> None:
        # 1) Если задан TARGET_CHAT_ID — работаем только в одной группе обсуждений
        if cfg.target_chat_id is not None and message.chat.id != cfg.target_chat_id:
            return

        # 2) Запрещаем медиа/файлы всем, кроме админов (photo/video/document)
        forbidden_kind = _detect_forbidden_media_kind(message)

        # пишем аудит самого факта детекта (помогает диагностике)
        if forbidden_kind:
            write_moderation_log(
                message,
                "media_detected",
                extra=f"type={forbidden_kind} from_user_present={message.from_user is not None}",
            )

        # удаляем медиа у не-админов
        if forbidden_kind and message.from_user is not None:
            if not await _is_admin(bot, message.chat.id, message.from_user.id):
                write_moderation_log(message, "media_non_admin", extra=f"type={forbidden_kind}")
                await safe_delete(message, log, f"media_non_admin:{forbidden_kind}")
                return

        # 3) Запрет сообщений от лица других каналов (sender_chat=channel)
        if cfg.delete_channel_messages and message.sender_chat is not None:
            if message.sender_chat.type == ChatType.CHANNEL:
                write_moderation_log(message, "channel_sender")
                await safe_delete(message, log, "channel_sender")
                return

        # 4) Антиреклама со ссылками (скоринг)
        d = decide(message, threshold=cfg.ad_score_threshold)
        if d.should_delete:
            extra = f"score={d.score} reasons={','.join(d.reasons)}"
            write_moderation_log(message, "ad_detected", extra=extra)
            await safe_delete(message, log, f"ad_score:{d.score}")
            return

    log.info(
        "Bot started. target_chat_id=%s delete_channel_messages=%s ad_score_threshold=%s",
        cfg.target_chat_id,
        cfg.delete_channel_messages,
        cfg.ad_score_threshold,
    )
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())