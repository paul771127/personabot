from __future__ import annotations

import base64
import hashlib
import hmac
import json

from personabot.adapters import LineAdapter, MessengerAdapter, TelegramAdapter
from personabot.config import LineConfig, MessengerConfig, Settings, TelegramConfig


def _settings(**kwargs) -> Settings:
    base = dict(
        db_path=":memory:",
        line=LineConfig(channel_secret="line-secret", access_token="line-token"),
        messenger=MessengerConfig(
            app_secret="fb-secret", page_access_token="fb-token", verify_token="fb-verify"
        ),
        telegram=TelegramConfig(bot_token="tg-token", webhook_secret="tg-secret"),
    )
    base.update(kwargs)
    return Settings(**base)


# ---- LINE ----


def test_line_signature_roundtrip():
    adapter = LineAdapter(_settings())
    body = b'{"events":[]}'
    sig = base64.b64encode(hmac.new(b"line-secret", body, hashlib.sha256).digest()).decode()
    assert adapter.verify(body, {"X-Line-Signature": sig})
    assert not adapter.verify(body, {"X-Line-Signature": "nope"})
    assert not adapter.verify(body + b" ", {"X-Line-Signature": sig})
    assert not adapter.verify(body, {})


def test_line_parse_keeps_only_one_to_one_text():
    adapter = LineAdapter(_settings())
    payload = {
        "events": [
            {
                "type": "message",
                "replyToken": "tok",
                "source": {"type": "user", "userId": "U1"},
                "message": {"type": "text", "text": "嗨"},
            },
            {  # 貼圖,忽略
                "type": "message",
                "source": {"type": "user", "userId": "U1"},
                "message": {"type": "sticker"},
            },
            {  # 群組,忽略
                "type": "message",
                "source": {"type": "group", "groupId": "G1"},
                "message": {"type": "text", "text": "嗨"},
            },
            {"type": "follow", "source": {"type": "user", "userId": "U1"}},
        ]
    }
    messages = adapter.parse(payload)
    assert len(messages) == 1
    assert messages[0].user_id == "U1"
    assert messages[0].reply_token == "tok"
    assert messages[0].platform == "line"


# ---- Messenger ----


def test_messenger_signature_roundtrip():
    adapter = MessengerAdapter(_settings())
    body = json.dumps({"object": "page"}).encode()
    digest = hmac.new(b"fb-secret", body, hashlib.sha256).hexdigest()
    assert adapter.verify(body, {"X-Hub-Signature-256": f"sha256={digest}"})
    assert not adapter.verify(body, {"X-Hub-Signature-256": "sha256=deadbeef"})
    assert not adapter.verify(body, {"X-Hub-Signature-256": digest})  # 少了前綴


def test_messenger_subscription_check():
    adapter = MessengerAdapter(_settings())
    assert adapter.verify_subscription("subscribe", "fb-verify")
    assert not adapter.verify_subscription("subscribe", "wrong")
    assert not adapter.verify_subscription("unsubscribe", "fb-verify")
    assert not adapter.verify_subscription("subscribe", "")


def test_messenger_parse_skips_echo():
    adapter = MessengerAdapter(_settings())
    payload = {
        "object": "page",
        "entry": [
            {
                "messaging": [
                    {"sender": {"id": "P1"}, "message": {"text": "嗨"}},
                    {"sender": {"id": "PAGE"}, "message": {"text": "回聲", "is_echo": True}},
                    {"sender": {"id": "P1"}, "delivery": {}},
                ]
            }
        ],
    }
    messages = adapter.parse(payload)
    assert [m.text for m in messages] == ["嗨"]


def test_messenger_ignores_non_page_objects():
    adapter = MessengerAdapter(_settings())
    assert adapter.parse({"object": "instagram", "entry": []}) == []


# ---- Telegram ----


def test_telegram_secret_token_check():
    adapter = TelegramAdapter(_settings())
    assert adapter.verify(b"{}", {"X-Telegram-Bot-Api-Secret-Token": "tg-secret"})
    assert not adapter.verify(b"{}", {"X-Telegram-Bot-Api-Secret-Token": "wrong"})
    assert not adapter.verify(b"{}", {})


def test_telegram_without_secret_accepts_everything():
    adapter = TelegramAdapter(_settings(telegram=TelegramConfig(bot_token="t", webhook_secret="")))
    assert adapter.verify(b"{}", {})


def test_telegram_parse_private_text():
    adapter = TelegramAdapter(_settings())
    update = {
        "update_id": 1,
        "message": {
            "chat": {"id": 42, "type": "private"},
            "from": {"first_name": "阿", "last_name": "明"},
            "text": "嗨",
        },
    }
    messages = adapter.parse(update)
    assert len(messages) == 1
    assert messages[0].user_id == "42"
    assert messages[0].display_name == "阿 明"


def test_telegram_ignores_groups_and_non_text():
    adapter = TelegramAdapter(_settings())
    assert adapter.parse({"message": {"chat": {"id": 1, "type": "group"}, "text": "嗨"}}) == []
    assert adapter.parse({"message": {"chat": {"id": 1, "type": "private"}}}) == []
    assert adapter.parse({"update_id": 1}) == []
