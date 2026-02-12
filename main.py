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

    @dp.message(F.text == "/chatid")
    async def chat_id_cmd(message: Message):
        await message.reply(f"chat_id = {message.chat.id}")

    @dp.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
    async def handle_group_message(message: Message) -> None:
        if cfg.target_chat_id is not None and message.chat.id != cfg.target_chat_id:
            return

        if message.sender_chat and message.sender_chat.type == ChatType.SUPERGROUP:
            return

        if message.from_user:
            try:
                member = await bot.get_chat_member(message.chat.id, message.from_user.id)
                if member.status in ("administrator", "creator"):
                    return
            except Exception as e:
                log.warning(
                    "Failed to check admin status: chat=%s user=%s err=%r",
                    message.chat.id,
                    message.from_user.id,
                    e,
                )
                return

        if (
            cfg.delete_channel_messages
            and message.sender_chat
            and message.sender_chat.type == ChatType.CHANNEL
        ):
            await message.delete()
            log.info(
                "Deleted channel message: chat=%s sender_chat_id=%s",
                message.chat.id,
                message.sender_chat.id,
            )
            return

        d = decide(message, threshold=cfg.ad_score_threshold)

        if not d.should_delete:
            return

        await message.delete()
        log.info(
            "Deleted ad message: chat=%s msg=%s score=%s reasons=%s",
            message.chat.id,
            message.message_id,
            d.score,
            ",".join(d.reasons),
        )

    log.info(
        "Bot started. target_chat_id=%s delete_channel_messages=%s ad_score_threshold=%s",
        cfg.target_chat_id,
        cfg.delete_channel_messages,
        cfg.ad_score_threshold,
    )

    await dp.start_polling(bot)

    @dp.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
    async def handle_group_message(message: Message) -> None:
        if cfg.target_chat_id is not None and message.chat.id != cfg.target_chat_id:
            return

        if message.video:
            try:
                member = await bot.get_chat_member(message.chat.id, message.from_user.id)
                if member.status not in ("administrator", "creator"):
                    await message.delete()
                    log.info(
                        "Deleted video from non-admin: user=%s chat=%s",
                        message.from_user.id,
                        message.chat.id,
                    )
                    return
            except Exception as e:
                log.warning("Video check failed: %r", e)

        if cfg.delete_channel_messages and message.sender_chat is not None:
            if message.sender_chat.type == ChatType.CHANNEL:
                try:
                    await message.delete()
                    log.info(
                        "Deleted channel-sender message: msg=%s chat=%s",
                        message.message_id,
                        message.chat.id,
                    )
                except Exception as e:
                    log.warning("Failed to delete channel-sender msg: %r", e)
                return

        d = decide(message, threshold=cfg.ad_score_threshold)

        if d.should_delete:
            try:
                await message.delete()
                log.info(
                    "Deleted ad message: user=%s score=%s",
                    getattr(message.from_user, "id", None),
                    d.score,
                )
            except Exception as e:
                log.warning("Failed to delete ad message: %r", e)


if __name__ == "__main__":
    asyncio.run(main())
