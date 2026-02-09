import os
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


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set in .env")

    chat_id_raw = os.getenv("TARGET_CHAT_ID")
    chat_id = int(chat_id_raw) if chat_id_raw and chat_id_raw.strip() else None

    delete_channel_messages = _as_bool(os.getenv("DELETE_CHANNEL_MESSAGES"), default=True)
    ad_score_threshold = _as_int(os.getenv("AD_SCORE_THRESHOLD"), default=2)
    log_level = (os.getenv("LOG_LEVEL") or "INFO").upper()

    return Config(
        bot_token=token,
        target_chat_id=chat_id,
        delete_channel_messages=delete_channel_messages,
        ad_score_threshold=ad_score_threshold,
        log_level=log_level,
    )
