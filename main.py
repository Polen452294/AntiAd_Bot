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
            f"chat_id={chat_id} msg_id={msg_id} "
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
    """
    try:
        await message.delete()
        log.info(
            "Deleted message. chat_id=%s message_id=%s reason=%s",
            message.chat.id,
            message.message_id,
            reason,
        )
        return True
    except TelegramBadRequest as e:
        log.info(
            "Skip delete (bad request). chat_id=%s message_id=%s reason=%s err=%s",
            message.chat.id,
            message.message_id,
            reason,
            e,
        )
        return False
    except TelegramForbiddenError as e:
        log.warning(
            "No rights to delete. chat_id=%s message_id=%s reason=%s err=%s",
            message.chat.id,
            message.message_id,
            reason,
            e,
        )
        return False
    except Exception as e:
        log.exception(
            "Unexpected delete error. chat_id=%s message_id=%s reason=%s err=%r",
            message.chat.id,
            message.message_id,
            reason,
            e,
        )
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
        if cfg.target_chat_id is not None and message.chat.id != cfg.target_chat_id:
            return

        if message.video and message.from_user is not None:
            try:
                member = await bot.get_chat_member(message.chat.id, message.from_user.id)
                if member.status not in ("administrator", "creator"):
                    write_moderation_log(message, "video_non_admin")
                    await safe_delete(message, log, "video_non_admin")
                    return
            except Exception as e:
                log.warning(
                    "Video admin check failed. chat_id=%s message_id=%s err=%r",
                    message.chat.id,
                    message.message_id,
                    e,
                )

        if cfg.delete_channel_messages and message.sender_chat is not None:
            if message.sender_chat.type == ChatType.CHANNEL:
                write_moderation_log(message, "channel_sender")
                await safe_delete(message, log, "channel_sender")
                return

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