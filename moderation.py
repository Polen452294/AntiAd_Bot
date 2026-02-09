from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from aiogram.types import Message, MessageEntity


URL_RE = re.compile(
    r"(?i)\b("
    r"https?://[^\s]+|"
    r"www\.[^\s]+|"
    r"t(?:\.|[ ]|[\[\(]?\.\]?|[․·∙•])?me/[^\s]+|"
    r"telegram\.me/[^\s]+|"
    r"(?:joinchat)/[^\s]+|"
    r"@[A-Za-z0-9_]{4,}"
    r")\b"
)

CONTACT_PHRASES = [
    "в лс", "в личку", "в личные", "пишите", "обращайтесь",
    "напишите", "свяжитесь",
]


TRIGGERS_STRONG = [
    "подпиш", "подписывай", "заходи", "вступай",
    "канал", "чат", "группа",
]

TRIGGERS_MONEY = [
    "скид", "акц", "промокод",
    "заработ", "доход", "инвест", "крипт",
]

SERVICE_TRIGGERS = [
    "помогу вам", "помогу с", "могу помочь",
    "решу проблему", "разберусь",
    "занимаюсь", "оказываю услуги",
    "есть опыт", "возьмусь",
    "сделаю под ключ", "готов помочь",
]


EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF]")


@dataclass
class ModerationDecision:
    should_delete: bool
    score: int
    reasons: list[str]


def _extract_text_and_entities(message: Message) -> tuple[str, list[MessageEntity]]:
    if message.text:
        return message.text, list(message.entities or [])
    if message.caption:
        return message.caption, list(message.caption_entities or [])
    return "", []


def _has_link_or_contact(text: str, entities: Iterable[MessageEntity]) -> bool:
    if URL_RE.search(text):
        return True
    for e in entities:
        if e.type in ("url", "text_link", "mention"):
            return True
    for p in CONTACT_PHRASES:
        if p in text:
            return True
    return False


def _count_hits(text: str, phrases: list[str]) -> int:
    return sum(1 for p in phrases if p in text)


def _lots_of_emoji_or_caps(text: str) -> bool:
    emoji_count = len(EMOJI_RE.findall(text))
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    upper_letters = [c for c in letters if c.isupper()]
    return emoji_count >= 4 or (len(upper_letters) / len(letters) >= 0.6)


def decide(message: Message, threshold: int = 2) -> ModerationDecision:
    text, entities = _extract_text_and_entities(message)
    text_l = text.lower()

    score = 0
    reasons: list[str] = []

    has_contact = _has_link_or_contact(text_l, entities)

    strong_hits = _count_hits(text_l, TRIGGERS_STRONG)
    money_hits = _count_hits(text_l, TRIGGERS_MONEY)

    if strong_hits:
        score += 2
        reasons.append(f"strong_ads:{strong_hits}")

    if money_hits:
        score += 1
        reasons.append(f"money_ads:{money_hits}")

    service_hits = _count_hits(text_l, SERVICE_TRIGGERS)
    if service_hits and has_contact:
        score += 2
        reasons.append(f"service_offer:{service_hits}")

    if _lots_of_emoji_or_caps(text):
        score += 1
        reasons.append("emoji_or_caps")

    should_delete = score >= threshold
    return ModerationDecision(should_delete=should_delete, score=score, reasons=reasons)
