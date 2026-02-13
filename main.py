import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from config import load_config
from moderation import decide


async def safe_delete(message: Message, log: logging.Logger, reason: str) -> bool:
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

        if message.from_user is None and message.sender_chat is None:
            return

        if message.video and message.from_user is not None:
            try:
                member = await bot.get_chat_member(message.chat.id, message.from_user.id)
                if member.status not in ("administrator", "creator"):
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
                await safe_delete(message, log, "channel_sender")
                return

        d = decide(message, threshold=cfg.ad_score_threshold)

        if d.has_link:
            log.info(
                "Decision. chat_id=%s message_id=%s from_user=%s score=%s delete=%s reasons=%s text=%r",
                message.chat.id,
                message.message_id,
                getattr(message.from_user, "id", None),
                d.score,
                d.should_delete,
                ",".join(d.reasons),
                (message.text or message.caption or "")[:120],
            )

        if d.should_delete:
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