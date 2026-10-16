from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from personabot.app import build_app
from personabot.config import reset_settings
from personabot.engine import Engine
from personabot.store import Store


@pytest.fixture
def client(monkeypatch, settings, persona, brain):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "line-secret")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "line-token")
    monkeypatch.setenv("MESSENGER_APP_SECRET", "fb-secret")
    monkeypatch.setenv("MESSENGER_PAGE_ACCESS_TOKEN", "fb-token")
    monkeypatch.setenv("MESSENGER_VERIFY_TOKEN", "fb-verify")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "tg-secret")
    reset_settings()

    engine = Engine(settings=settings, persona=persona, store=Store(":memory:"), brain=brain)
    app = build_app(engine)
    with TestClient(app) as c:
        c.engine = engine  # 測試裡方便拿來斷言
        yield c


def test_healthz(client):
    body = client.get("/healthz").json()
    assert body["ok"] is True
    assert body["platforms"]["line"] is True


def test_healthz_reports_missing_api_key(monkeypatch, settings, persona, brain):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    reset_settings()
    engine = Engine(settings=settings, persona=persona, store=Store(":memory:"), brain=brain)
    with TestClient(build_app(engine)) as c:
        assert c.get("/healthz").json()["ok"] is False


def test_line_webhook_rejects_bad_signature(client):
    response = client.post("/webhook/line", content=b'{"events":[]}', headers={"X-Line-Signature": "x"})
    assert response.status_code == 403


def test_line_webhook_accepts_good_signature(client, monkeypatch):
    sent: list = []

    async def fake_send(inbound, reply):
        sent.append((inbound.user_id, reply.bubbles))

    async def fake_name(user_id):
        return "阿明"

    monkeypatch.setattr(client.app.state.adapters["line"], "send", fake_send)
    monkeypatch.setattr(client.app.state.adapters["line"], "fetch_display_name", fake_name)

    payload = {
        "events": [
            {
                "type": "message",
                "replyToken": "tok",
                "source": {"type": "user", "userId": "U1"},
                "message": {"type": "text", "text": "嗨"},
            }
        ]
    }
    body = json.dumps(payload).encode()
    sig = base64.b64encode(hmac.new(b"line-secret", body, hashlib.sha256).digest()).decode()

    response = client.post("/webhook/line", content=body, headers={"X-Line-Signature": sig})

    assert response.status_code == 200
    assert sent == [("U1", ["嗯。"])]


def test_messenger_subscription_handshake(client):
    response = client.get(
        "/webhook/messenger",
        params={"hub.mode": "subscribe", "hub.verify_token": "fb-verify", "hub.challenge": "1234"},
    )
    assert response.status_code == 200
    assert response.text == "1234"

    bad = client.get(
        "/webhook/messenger",
        params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1234"},
    )
    assert bad.status_code == 403


def test_telegram_webhook_requires_secret_header(client):
    body = json.dumps({"message": {"chat": {"id": 1, "type": "private"}, "text": "嗨"}}).encode()
    assert client.post("/webhook/telegram", content=body).status_code == 403
    ok = client.post(
        "/webhook/telegram",
        content=body,
        headers={"X-Telegram-Bot-Api-Secret-Token": "tg-secret"},
    )
    assert ok.status_code == 200


def test_malformed_json_is_rejected(client):
    sig = base64.b64encode(hmac.new(b"line-secret", b"not json", hashlib.sha256).digest()).decode()
    response = client.post("/webhook/line", content=b"not json", headers={"X-Line-Signature": sig})
    assert response.status_code == 400
