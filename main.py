import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType
from aiogram.types import Message

from config import load_config
from moderation import decide


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
        # Ограничение по конкретной группе (если задано)
        if cfg.target_chat_id is not None and message.chat.id != cfg.target_chat_id:
            return

        # 1) Запрет сообщений "от лица каналов"
        # Важно: anonymous admin обычно sender_chat = supergroup (а не channel) — его не трогаем.
        if cfg.delete_channel_messages and message.sender_chat is not None:
            if message.sender_chat.type == ChatType.CHANNEL:
                try:
                    await message.delete()
                    log.info(
                        "Deleted channel-sender message: msg=%s chat=%s sender_chat_id=%s title=%r",
                        message.message_id,
                        message.chat.id,
                        message.sender_chat.id,
                        getattr(message.sender_chat, "title", None),
                    )
                except Exception as e:
                    log.warning(
                        "Failed to delete channel-sender msg=%s in chat=%s: %r",
                        message.message_id,
                        message.chat.id,
                        e,
                    )
                return  # уже обработали

        # 2) Антиреклама со ссылками (скоринг)
        d = decide(message, threshold=cfg.ad_score_threshold)

        if d.has_link:
            log.info(
                "Decision: msg=%s chat=%s from=%s score=%s delete=%s reasons=%s text=%r",
                message.message_id,
                message.chat.id,
                getattr(message.from_user, "id", None),
                d.score,
                d.should_delete,
                ",".join(d.reasons),
                (message.text or message.caption or "")[:120],
            )

        if not d.should_delete:
            return

        try:
            await message.delete()
        except Exception as e:
            log.warning(
                "Failed to delete message_id=%s in chat=%s: %r",
                message.message_id,
                message.chat.id,
                e,
            )

    logging.getLogger("antiad").info(
        "Bot started. target_chat_id=%s delete_channel_messages=%s ad_score_threshold=%s",
        cfg.target_chat_id,
        cfg.delete_channel_messages,
        cfg.ad_score_threshold,
    )
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
