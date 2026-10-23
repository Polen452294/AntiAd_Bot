from __future__ import annotations

import hashlib
import re
import time
import unicodedata
from collections import OrderedDict, deque
from collections.abc import Iterator
from dataclasses import dataclass

from aiogram.types import Message, MessageEntity

URL_RE = re.compile(
    r"(?:https?|hxxps?|ftp|tg)://\S+"
    r"|\bwww\.\S+"
    r"|\bjoinchat/\S+"
    r"|(?<!\w)(?:[a-zа-яё0-9](?:[a-zа-яё0-9-]{0,61}[a-zа-яё0-9])?\.)+"
    r"(?:[a-zа-яё]{2,63}|xn--[a-z0-9-]{2,59})(?![\w-])"
    r"|(?<!\w)@[a-z0-9_]{4,32}\b",
    re.IGNORECASE,
)
OBFUSCATED_DOT_RE = re.compile(r"[\[(]\s*(?:\.|dot|точка)\s*[\])]", re.I)
TELEGRAM_SPACE_RE = re.compile(r"\b(t|telegram)\s+(me)\s*/", re.I)
LINK_ENTITIES = {"url", "text_link", "mention", "text_mention", "email"}
FORBIDDEN_MEDIA = (
    "photo",
    "video",
    "document",
    "animation",
    "video_note",
    "audio",
    "voice",
    "sticker",
    "paid_media",
    "live_photo",
)

CONTACT_RE = re.compile(r"\b(?:в\s+(?:лс|личку|личные)|пишите|напишите|обращайтесь|свяжитесь)\b")
CALL_TO_ACTION_RE = re.compile(
    r"\b(?:подпиш(?:ись|итесь)|подписывай(?:ся|тесь))\b"
    r"|\b(?:заходи(?:те)?|вступай(?:те)?|переходи(?:те)?)\b[\s\S]{0,80}"
    r"\b(?:канал\w*|чат\w*|групп\w*|профил\w*|ссылк\w*)\b"
)
MONEY_RE = re.compile(
    r"\b(?:скидк\w*|акци[яиюей]\w*|промокод\w*|заработ\w*|доход\w*|инвест\w*|крипт\w*"
    r"|продам|продаю|продажа)\b"
)
SERVICE_RE = re.compile(
    r"\b(?:помогу\s+(?:вам|с)|могу помочь|решу проблему|разберусь|занимаюсь|"
    r"оказываю услуги|есть опыт|возьмусь|сделаю под ключ|готов помочь)\b"
)
EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF]")


@dataclass
class ModerationDecision:
    should_delete: bool
    score: int
    reasons: list[str]


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    text = text.translate(str.maketrans({"․": ".", "·": ".", "∙": ".", "•": ".", "。": "."}))
    text = OBFUSCATED_DOT_RE.sub(".", text)
    return TELEGRAM_SPACE_RE.sub(r"\1.\2/", text)


def _rich_parts(message: Message) -> Iterator[dict | str]:
    rich = getattr(message, "rich_message", None)
    stack = [rich.model_dump(exclude_none=True) if hasattr(rich, "model_dump") else rich]
    while stack:
        part = stack.pop()
        if isinstance(part, dict):
            yield part
            stack.extend(value for key, value in reversed(list(part.items())) if key != "type")
        elif isinstance(part, list):
            stack.extend(reversed(part))
        elif isinstance(part, str):
            yield part


def _extract_text_and_entities(message: Message) -> tuple[str, list[MessageEntity]]:
    if message.text:
        return message.text, list(message.entities or [])
    if message.caption:
        return message.caption, list(message.caption_entities or [])
    return " ".join(part for part in _rich_parts(message) if isinstance(part, str)), []


def _lots_of_emoji_or_caps(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return len(EMOJI_RE.findall(text)) >= 4 or (
        len(letters) >= 10 and sum(c.isupper() for c in letters) / len(letters) >= 0.6
    )


def decide(message: Message, threshold: int = 2) -> ModerationDecision:
    """Classify content. The transport must exempt administrators first."""
    for kind in FORBIDDEN_MEDIA:
        if getattr(message, kind, None):
            return ModerationDecision(True, 0, [f"media_forbidden:{kind}"])
    for part in _rich_parts(message):
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind in {*FORBIDDEN_MEDIA, "voice_note"}:
            return ModerationDecision(True, 0, [f"media_forbidden:{kind}"])
        if kind in LINK_ENTITIES | {"email_address"} or part.get("url"):
            return ModerationDecision(True, 0, ["link_or_mention"])

    text, entities = _extract_text_and_entities(message)
    normalized = normalize_text(text)
    if any(entity.type in LINK_ENTITIES for entity in entities) or URL_RE.search(normalized):
        return ModerationDecision(True, 0, ["link_or_mention"])
    if message.reply_markup and any(
        button.url or button.login_url or button.web_app
        for row in message.reply_markup.inline_keyboard
        for button in row
    ):
        return ModerationDecision(True, 0, ["button_link"])

    score = 0
    reasons: list[str] = []
    if CALL_TO_ACTION_RE.search(normalized):
        score += 2
        reasons.append("advertising_call_to_action")
    has_money = bool(MONEY_RE.search(normalized))
    has_contact = bool(CONTACT_RE.search(normalized))
    if has_money:
        score += 1
        reasons.append("money_offer")
    if has_contact and (has_money or SERVICE_RE.search(normalized)):
        score += 2
        reasons.append("contact_offer")
    if _lots_of_emoji_or_caps(text):
        score += 1
        reasons.append("emoji_or_caps")
    return ModerationDecision(score >= threshold, score, reasons)


@dataclass
class _SpamEntry:
    timestamp: float
    message_id: int
    fingerprint: bytes


class SpamTracker:
    """Bounded, per-chat/per-sender history; edits never count as new posts."""

    def __init__(
        self,
        repeat_limit: int = 3,
        repeat_window: int = 60,
        flood_limit: int = 6,
        flood_window: int = 10,
        max_senders: int = 10_000,
    ) -> None:
        self.repeat_limit = repeat_limit
        self.repeat_window = repeat_window
        self.flood_limit = flood_limit
        self.flood_window = flood_window
        self.max_senders = max_senders
        self._history: OrderedDict[tuple[int, int], deque[_SpamEntry]] = OrderedDict()

    def check(self, message: Message, now: float | None = None) -> str | None:
        if message.edit_date or not message.from_user or message.sender_chat:
            return None
        text, _ = _extract_text_and_entities(message)
        if not text:
            return None
        now = time.monotonic() if now is None else now
        longest_window = max(self.repeat_window, self.flood_window)
        while self._history:
            first_key = next(iter(self._history))
            if now - self._history[first_key][-1].timestamp <= longest_window:
                break
            self._history.popitem(last=False)
        key = (message.chat.id, message.from_user.id)
        history = self._history.pop(key, deque(maxlen=100))
        self._history[key] = history
        while history and now - history[0].timestamp > longest_window:
            history.popleft()
        fingerprint = hashlib.sha256(" ".join(normalize_text(text).split()).encode()).digest()
        # A repeated update must not inflate counters or bypass a failed deletion retry.
        if not any(entry.message_id == message.message_id for entry in history):
            history.append(_SpamEntry(now, message.message_id, fingerprint))
        while len(self._history) > self.max_senders:
            self._history.popitem(last=False)
        repeats = sum(
            entry.fingerprint == fingerprint and now - entry.timestamp <= self.repeat_window
            for entry in history
        )
        if repeats >= self.repeat_limit:
            return "repeated_spam"
        if sum(now - entry.timestamp <= self.flood_window for entry in history) >= self.flood_limit:
            return "message_flood"
        return None
