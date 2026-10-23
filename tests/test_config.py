import pytest

from config import load_config


@pytest.fixture(autouse=True)
def clean_config(monkeypatch):
    for name in (
        "TARGET_CHAT_ID",
        "DELETE_CHANNEL_MESSAGES",
        "AD_SCORE_THRESHOLD",
        "LOG_LEVEL",
        "TEST_MODE_DELETE_ADMINS",
        "SPAM_REPEAT_LIMIT",
        "SPAM_REPEAT_WINDOW",
        "SPAM_FLOOD_LIMIT",
        "SPAM_FLOOD_WINDOW",
        "DELETION_LOG_PATH",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BOT_TOKEN", "123456:test")


def test_defaults_keep_admins_exempt_and_enable_channel_protection():
    cfg = load_config()
    assert cfg.delete_channel_messages
    assert cfg.spam_repeat_limit == 3
    assert cfg.spam_flood_limit == 6


def test_legacy_admin_test_mode_is_ignored(monkeypatch):
    monkeypatch.setenv("TEST_MODE_DELETE_ADMINS", "true")
    with pytest.warns(UserWarning, match="администраторы всегда исключены"):
        cfg = load_config()
    assert not hasattr(cfg, "test_mode_delete_admins")


@pytest.mark.parametrize(
    "name,value",
    [
        ("AD_SCORE_THRESHOLD", "0"),
        ("SPAM_REPEAT_LIMIT", "0"),
        ("SPAM_REPEAT_WINDOW", "-1"),
        ("SPAM_FLOOD_LIMIT", "101"),
        ("SPAM_REPEAT_LIMIT", "101"),
        ("SPAM_FLOOD_WINDOW", "0"),
    ],
)
def test_invalid_thresholds_are_rejected(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        load_config()


def test_missing_token_is_rejected(monkeypatch):
    monkeypatch.delenv("BOT_TOKEN")
    with pytest.raises(RuntimeError, match="BOT_TOKEN"):
        load_config()
