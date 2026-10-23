import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import ChatMember, ChatMemberUpdated, Message

from config import Config, load_config
from moderation import SpamTracker, decide

ADMIN_STATUSES = {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}
GROUP_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}
MAX_RETRY_DELAY = 5


async def safe_delete(message: Message, log: logging.Logger, reason: str) -> bool:
    for attempt in range(3):
        try:
            await message.delete()
            log.info(
                "DELETE SUCCESS | chat_id=%s | msg_id=%s | reason=%s",
                message.chat.id,
                message.message_id,
                reason,
            )
            return True
        except TelegramRetryAfter as error:
            if attempt < 2 and error.retry_after <= MAX_RETRY_DELAY:
                await asyncio.sleep(error.retry_after)
                continue
            log.error(
                "DELETE RATE LIMITED | chat_id=%s | retry_after=%s",
                message.chat.id,
                error.retry_after,
            )
        except (TelegramNetworkError, TelegramServerError):
            if attempt < 2:
                await asyncio.sleep(attempt + 1)
                continue
            log.error(
                "DELETE NETWORK ERROR | chat_id=%s | msg_id=%s", message.chat.id, message.message_id
            )
        except TelegramForbiddenError:
            log.error(
                "DELETE FORBIDDEN | chat_id=%s | Проверьте доступ бота к группе", message.chat.id
            )
        except TelegramBadRequest as error:
            if "message to delete not found" in error.message.lower():
                return True  # Already removed by another moderator or a repeated update.
            log.error(
                "DELETE FAILED | chat_id=%s | msg_id=%s | err=%s | "
                "Назначьте бота администратором с правом удаления сообщений",
                message.chat.id,
                message.message_id,
                error.message,
            )
        except TelegramAPIError as error:
            log.error(
                "DELETE API ERROR | chat_id=%s | msg_id=%s | err=%s",
                message.chat.id,
                message.message_id,
                error.message,
            )
        return False
    return False


def can_delete_messages(member: ChatMember, chat_type: str) -> bool:
    return member.status == ChatMemberStatus.CREATOR or (
        member.status == ChatMemberStatus.ADMINISTRATOR
        and (chat_type == ChatType.GROUP or getattr(member, "can_delete_messages", False))
    )


def create_dispatcher(cfg: Config) -> Dispatcher:
    log = logging.getLogger("antiad")
    dp = Dispatcher()
    spam = SpamTracker(
        cfg.spam_repeat_limit,
        cfg.spam_repeat_window,
        cfg.spam_flood_limit,
        cfg.spam_flood_window,
    )

    @dp.message(F.chat.type.in_(GROUP_TYPES))
    @dp.edited_message(F.chat.type.in_(GROUP_TYPES))
    async def handle_group_message(message: Message, bot: Bot) -> None:
        if cfg.target_chat_id is not None and message.chat.id != cfg.target_chat_id:
            return

        # sender_chat takes precedence: Telegram supplies a fake from_user here.
        if message.sender_chat:
            if message.sender_chat.id == message.chat.id or message.is_automatic_forward:
                return
            if cfg.delete_channel_messages:
                await safe_delete(message, log, "channel_sender")
                return
        elif message.from_user:
            try:
                member = await bot.get_chat_member(message.chat.id, message.from_user.id)
            except TelegramAPIError as error:
                # Never risk deleting an administrator's post on a failed status lookup.
                log.error(
                    "ADMIN CHECK FAILED | chat_id=%s | user_id=%s | err=%s | "
                    "Сообщение пропущено; проверьте права администратора у бота",
                    message.chat.id,
                    message.from_user.id,
                    error.message,
                )
                return
            if member.status in ADMIN_STATUSES:
                return
        else:
            return

        decision = decide(message, cfg.ad_score_threshold)
        spam_reason = spam.check(message)
        if decision.should_delete or spam_reason:
            reasons = decision.reasons if decision.should_delete else []
            reason = ",".join(reasons + ([spam_reason] if spam_reason else []))
            await safe_delete(message, log, reason)

    @dp.my_chat_member(F.chat.type.in_(GROUP_TYPES))
    async def handle_bot_rights(event: ChatMemberUpdated) -> None:
        if cfg.target_chat_id is not None and event.chat.id != cfg.target_chat_id:
            return
        if can_delete_messages(event.new_chat_member, event.chat.type):
            log.info("BOT READY | chat_id=%s | Удаление сообщений доступно", event.chat.id)
        else:
            log.error(
                "BOT NOT READY | chat_id=%s | "
                "Назначьте бота администратором с правом удаления сообщений",
                event.chat.id,
            )

    return dp


async def check_target_chat(bot: Bot, cfg: Config) -> None:
    if cfg.target_chat_id is None:
        return
    chat = await bot.get_chat(cfg.target_chat_id)
    if chat.type not in GROUP_TYPES:
        raise RuntimeError("TARGET_CHAT_ID должен указывать на группу обсуждений, а не канал")
    member = await bot.get_chat_member(chat.id, bot.id)
    if not can_delete_messages(member, chat.type):
        raise RuntimeError(
            "Бот не может удалять сообщения. Назначьте его администратором "
            "группы с правом удаления сообщений."
        )


async def main() -> None:
    cfg = load_config()
    logging.basicConfig(
        level=getattr(logging, cfg.log_level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    log = logging.getLogger("antiad")
    log.info("BOT START | target_chat_id=%s", cfg.target_chat_id)
    dp = create_dispatcher(cfg)
    async with Bot(token=cfg.bot_token) as bot:
        await check_target_chat(bot, cfg)
        # This application uses polling; preserve queued updates when switching from webhook.
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
