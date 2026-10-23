import pytest

from moderation import SpamTracker, decide


@pytest.mark.parametrize(
    "text",
    [
        "https://example.com",
        "example.org/page",
        "@some_channel",
        "t[.]me/spam",
        "t me/spam",
        "t\u200b.me/spam",
        "t․me/spam",
        "t(dot)me/spam",
        "telegram me/spam",
        "www.example.com",
        "https://127.0.0.1:8080/path",
        "tg://resolve?domain=spam",
        "пример.рф",
        "mail@example.com",
    ],
)
def test_links_are_deleted_without_advertising_words(message_factory, text):
    assert decide(message_factory(text=text)).should_delete


def test_hidden_link_is_deleted(message_factory):
    message = message_factory(
        text="Смотрите здесь",
        entities=[{"type": "text_link", "offset": 9, "length": 5, "url": "https://example.com"}],
    )
    assert decide(message).should_delete


def test_normal_discussion_is_kept(message_factory):
    assert not decide(message_factory(text="Этот канал интересно читать")).should_delete


@pytest.mark.parametrize("kind", ["url", "text_link", "mention", "text_mention", "email"])
@pytest.mark.parametrize("caption", [False, True])
def test_entity_links_in_text_and_captions(message_factory, kind, caption):
    entity = {"type": kind, "offset": 0, "length": 5}
    if kind == "text_link":
        entity["url"] = "https://example.com"
    if kind == "text_mention":
        entity["user"] = {"id": 99, "first_name": "Name", "is_bot": False}
    fields = (
        {"text": None, "caption": "здесь", "caption_entities": [entity]}
        if caption
        else {"text": "здесь", "entities": [entity]}
    )
    assert decide(message_factory(**fields)).should_delete


@pytest.mark.parametrize(
    "kind,media",
    [
        ("photo", [{"file_id": "x", "file_unique_id": "x", "width": 1, "height": 1}]),
        ("document", {"file_id": "x", "file_unique_id": "x"}),
        ("video", {"file_id": "x", "file_unique_id": "x", "width": 1, "height": 1, "duration": 1}),
        (
            "animation",
            {"file_id": "x", "file_unique_id": "x", "width": 1, "height": 1, "duration": 1},
        ),
        ("audio", {"file_id": "x", "file_unique_id": "x", "duration": 1}),
        ("voice", {"file_id": "x", "file_unique_id": "x", "duration": 1}),
        ("video_note", {"file_id": "x", "file_unique_id": "x", "duration": 1, "length": 1}),
        (
            "sticker",
            {
                "file_id": "x",
                "file_unique_id": "x",
                "type": "regular",
                "width": 1,
                "height": 1,
                "is_animated": False,
                "is_video": False,
            },
        ),
        ("paid_media", {"star_count": 1, "paid_media": [{"type": "preview"}]}),
    ],
)
def test_media_are_always_deleted(message_factory, kind, media):
    decision = decide(message_factory(text=None, **{kind: media}), threshold=999)
    assert decision.should_delete
    assert decision.reasons == [f"media_forbidden:{kind}"]


def test_url_button_is_deleted(message_factory):
    message = message_factory(
        reply_markup={"inline_keyboard": [[{"text": "Открыть", "url": "https://example.com"}]]}
    )
    assert decide(message).should_delete


@pytest.mark.parametrize(
    "text",
    [
        "Подписывайтесь на наш канал!",
        "Помогу с ремонтом, пишите в личку",
        "Заработок каждый день, напишите в лс",
        "СКИДКИ НА ВСЁ 🔥🔥🔥🔥",
    ],
)
def test_advertising_without_links(message_factory, text):
    assert decide(message_factory(text=text)).should_delete


@pytest.mark.parametrize(
    "text",
    [
        "Спасибо!",
        "В чате сегодня тихо",
        "Нашёл опечатку в статье",
        "Есть опыт работы с Python",
        "Какие скидки были вчера?",
        "😀😀😀😀",
    ],
)
def test_normal_messages_do_not_trigger_advertising(message_factory, text):
    assert not decide(message_factory(text=text)).should_delete


def test_links_cannot_be_disabled_with_ad_threshold(message_factory):
    assert decide(message_factory(text="example.com"), threshold=999).should_delete


def test_repeated_spam_and_normalization(message_factory):
    tracker = SpamTracker()
    assert tracker.check(message_factory(message_id=1, text="Привет  всем"), now=0) is None
    assert tracker.check(message_factory(message_id=2, text="ПРИВЕТ всем"), now=1) is None
    assert (
        tracker.check(message_factory(message_id=3, text="Привет всем"), now=2) == "repeated_spam"
    )


def test_flood(message_factory):
    tracker = SpamTracker()
    for index in range(5):
        assert (
            tracker.check(message_factory(message_id=index, text=f"Сообщение {index}"), now=index)
            is None
        )
    assert (
        tracker.check(message_factory(message_id=6, text="Другое сообщение"), now=5)
        == "message_flood"
    )


def test_old_spam_expires(message_factory):
    tracker = SpamTracker()
    for index, now in enumerate([0, 1, 62]):
        assert tracker.check(message_factory(message_id=index), now=now) is None


def test_spam_is_separate_for_users_and_chats(message_factory):
    tracker = SpamTracker()
    assert tracker.check(message_factory(message_id=1), now=0) is None
    assert tracker.check(message_factory(message_id=2), now=1) is None
    assert (
        tracker.check(
            message_factory(
                message_id=3, **{"from": {"id": 43, "is_bot": False, "first_name": "Other"}}
            ),
            now=2,
        )
        is None
    )
    assert (
        tracker.check(
            message_factory(message_id=3, chat={"id": -100999999, "type": "supergroup"}), now=2
        )
        is None
    )


def test_duplicate_updates_and_edits_do_not_inflate_spam(message_factory):
    tracker = SpamTracker()
    message = message_factory()
    for _ in range(5):
        assert tracker.check(message, now=0) is None
    assert (
        tracker.check(message_factory(message_id=2, edit_date=int(message.date.timestamp())), now=1)
        is None
    )
    assert tracker.check(message_factory(message_id=3), now=2) is None


def test_spam_tracker_has_bounded_memory(message_factory):
    tracker = SpamTracker(max_senders=2)
    for user_id in range(10):
        tracker.check(
            message_factory(**{"from": {"id": user_id, "is_bot": False, "first_name": "User"}}),
            now=0,
        )
    assert len(tracker._history) == 2


@pytest.mark.parametrize("text", ["Подпишу бумаги завтра", "Переходите дорогу на зелёный"])
def test_unrelated_verbs_are_not_advertising(message_factory, text):
    assert not decide(message_factory(text=text)).should_delete


@pytest.mark.parametrize(
    "block",
    [
        {
            "type": "photo",
            "photo": [{"file_id": "x", "file_unique_id": "x", "width": 1, "height": 1}],
        },
        {"type": "document", "document": {"file_id": "x", "file_unique_id": "x"}},
        {
            "type": "paragraph",
            "text": {"type": "url", "text": "сюда", "url": "https://example.com"},
        },
        {"type": "paragraph", "text": "example.com"},
        {"type": "paragraph", "text": "Подписывайтесь на наш канал"},
    ],
)
def test_rich_content_cannot_bypass_filters(message_factory, block):
    assert decide(message_factory(text=None, rich_message={"blocks": [block]})).should_delete


def test_normal_rich_text_is_kept(message_factory):
    message = message_factory(
        text=None,
        rich_message={
            "blocks": [
                {"type": "paragraph", "text": ["Привет, ", {"type": "bold", "text": "друзья"}]}
            ]
        },
    )
    assert not decide(message).should_delete


def test_live_photo_is_deleted(message_factory):
    message = message_factory(
        text=None,
        live_photo={"file_id": "x", "file_unique_id": "x", "width": 1, "height": 1, "duration": 1},
    )
    assert decide(message).should_delete
