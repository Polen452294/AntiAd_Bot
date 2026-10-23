import os
import warnings
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_int(value: str | None, default: int) -> int:
    if value is None or not value.strip():
        return default
    return int(value)


@dataclass(frozen=True)
class Config:
    bot_token: str
    target_chat_id: int | None

    delete_channel_messages: bool
    ad_score_threshold: int

    log_level: str
    spam_repeat_limit: int = 3
    spam_repeat_window: int = 60
    spam_flood_limit: int = 6
    spam_flood_window: int = 10
    deletion_log_path: str = "data/deleted_messages.jsonl"


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set in .env")

    chat_id_raw = os.getenv("TARGET_CHAT_ID")
    chat_id = int(chat_id_raw) if chat_id_raw and chat_id_raw.strip() else None

    delete_channel_messages = _as_bool(os.getenv("DELETE_CHANNEL_MESSAGES"), default=True)
    ad_score_threshold = _as_int(os.getenv("AD_SCORE_THRESHOLD"), default=2)
    log_level = (os.getenv("LOG_LEVEL") or "INFO").upper()

    if _as_bool(os.getenv("TEST_MODE_DELETE_ADMINS")):
        warnings.warn(
            "TEST_MODE_DELETE_ADMINS больше не применяется: администраторы всегда исключены",
            stacklevel=2,
        )

    spam_settings = {
        name: _as_int(os.getenv(name.upper()), default)
        for name, default in (
            ("spam_repeat_limit", 3),
            ("spam_repeat_window", 60),
            ("spam_flood_limit", 6),
            ("spam_flood_window", 10),
        )
    }
    if ad_score_threshold < 1 or any(value < 1 for value in spam_settings.values()):
        raise ValueError("Порог рекламы и параметры антиспама должны быть положительными")
    if spam_settings["spam_repeat_limit"] > 100 or spam_settings["spam_flood_limit"] > 100:
        raise ValueError("Лимиты антиспама не могут превышать 100 сообщений")

    return Config(
        bot_token=token,
        target_chat_id=chat_id,
        delete_channel_messages=delete_channel_messages,
        ad_score_threshold=ad_score_threshold,
        log_level=log_level,
        deletion_log_path=(os.getenv("DELETION_LOG_PATH") or "data/deleted_messages.jsonl").strip(),
        **spam_settings,
    )
