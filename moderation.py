from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from aiogram.types import Message, MessageEntity


URL_RE = re.compile(
    r"(?i)("
    r"https?://\S+|"
    r"www\.\S+|"
    r"(?:t(?:\.|[ ]|[\[\(]?\.\]?|[․·∙•])?me)/\S+|"
    r"(?:telegram\.me)/\S+|"
    r"(?:joinchat)/\S+|"
    r"(?:\+)[A-Za-z0-9_-]{10,}"
    r")"
)

TRIGGERS_STRONG = [
    "подпиш", "подписывай", "заходи", "вступай", "присоединяй",
    "канал", "чат", "группа",
]
TRIGGERS_MONEY = [
    "скид", "акц", "промокод",
    "заработ", "доход", "инвест", "крипт",
    "в профил", "в шапк", "bio",
]

EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF]")


@dataclass
class ModerationDecision:
    should_delete: bool
    score: int
    has_link: bool
    reasons: list[str]


def _extract_text_and_entities(message: Message) -> tuple[str, list[MessageEntity]]:
    if message.text:
        return message.text, list(message.entities or [])
    if message.caption:
        return message.caption, list(message.caption_entities or [])
    return "", []


def _has_link_via_entities(entities: Iterable[MessageEntity]) -> bool:
    for e in entities:
        if e.type in ("url", "text_link", "mention"):
            return True
    return False


def _has_link_via_regex(text: str) -> bool:
    return bool(URL_RE.search(text))


def _count_trigger_hits(text_l: str, triggers: list[str]) -> int:
    return sum(1 for t in triggers if t in text_l)


def _is_short_with_link(text: str) -> bool:
    return len(text.strip()) < 40 and _has_link_via_regex(text)


def _lots_of_emoji_or_caps(text: str) -> bool:
    emoji_count = len(EMOJI_RE.findall(text))
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    upper_letters = [c for c in letters if c.isupper()]
    caps_ratio = len(upper_letters) / max(1, len(letters))
    return (emoji_count >= 4) or (caps_ratio >= 0.6 and len(letters) >= 12)


def decide(message: Message, threshold: int = 2) -> ModerationDecision:
    text, entities = _extract_text_and_entities(message)
    text_l = text.lower()

    has_link = _has_link_via_entities(entities) or _has_link_via_regex(text)

    score = 0
    reasons: list[str] = []

    if has_link:
        reasons.append("link_detected")

        strong_hits = _count_trigger_hits(text_l, TRIGGERS_STRONG)
        money_hits = _count_trigger_hits(text_l, TRIGGERS_MONEY)

        if strong_hits:
            score += 2
            reasons.append(f"strong_triggers:{strong_hits}")

        if money_hits:
            score += 1
            reasons.append(f"money_triggers:{money_hits}")

        if _is_short_with_link(text):
            score += 1
            reasons.append("short_with_link")

        if _lots_of_emoji_or_caps(text):
            score += 1
            reasons.append("emoji_or_caps")

    should_delete = has_link and score >= threshold
    return ModerationDecision(should_delete=should_delete, score=score, has_link=has_link, reasons=reasons)
