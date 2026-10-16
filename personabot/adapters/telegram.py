"""Telegram Bot API。

https://core.telegram.org/bots/api

Telegram 可以用 webhook,也可以用 long polling。本機開發時 polling 最方便:
不用公開網址,也不用設憑證。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Mapping

import httpx

from ..types import InboundMessage, Reply
from .base import Adapter, pace

log = logging.getLogger(__name__)

MAX_TEXT_LENGTH = 4000  # 官方上限 4096


class TelegramAdapter(Adapter):
    name = "telegram"

    @property
    def _api(self) -> str:
        return f"https://api.telegram.org/bot{self.settings.telegram.bot_token}"

    def verify(self, body: bytes, headers: Mapping[str, str]) -> bool:
        """Telegram 不簽 payload,而是回送我們設 webhook 時給的 secret token。"""
        secret = self.settings.telegram.webhook_secret
        if not secret:
            # 沒設 secret 就無從驗起。正式環境請務必設定。
            log.warning("TELEGRAM_WEBHOOK_SECRET 沒設,webhook 無法驗證來源")
            return True
        return hmac_equal(_header(headers, "x-telegram-bot-api-secret-token"), secret)

    def parse(self, payload: dict[str, Any]) -> list[InboundMessage]:
        message = payload.get("message") or payload.get("edited_message")
        if not message:
            return []
        text = message.get("text")
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if not text or chat_id is None:
            return []
        if chat.get("type") != "private":
            return []
        sender = message.get("from") or {}
        display_name = " ".join(
            p for p in [sender.get("first_name"), sender.get("last_name")] if p
        ) or sender.get("username")
        return [
            InboundMessage(
                platform=self.name,
                user_id=str(chat_id),
                text=text,
                display_name=display_name,
                raw=message,
            )
        ]

    async def send(self, inbound: InboundMessage, reply: Reply) -> None:
        for index, bubble in enumerate(reply.bubbles):
            if index > 0:
                await self.show_typing(inbound)
                await pace(self.settings, bubble)
            await self._post(
                f"{self._api}/sendMessage",
                json={"chat_id": inbound.user_id, "text": bubble[:MAX_TEXT_LENGTH]},
            )

    async def show_typing(self, inbound: InboundMessage, seconds: float = 3.0) -> None:
        if not self.settings.typing_indicator:
            return
        await self._post(
            f"{self._api}/sendChatAction",
            json={"chat_id": inbound.user_id, "action": "typing"},
        )

    # ---- 本機開發用的 long polling ----

    async def poll(
        self,
        handler: Callable[[InboundMessage], Awaitable[Reply]],
        stop: asyncio.Event | None = None,
    ) -> None:
        """一直抓新訊息並回覆,直到 stop 被設起來。不需要公開網址。"""
        offset: int | None = None
        stop = stop or asyncio.Event()
        log.info("Telegram long polling 開始")
        while not stop.is_set():
            try:
                response = await self.client.get(
                    f"{self._api}/getUpdates",
                    params={"timeout": 30, **({"offset": offset} if offset else {})},
                    timeout=40.0,
                )
                data = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("getUpdates 失敗,三秒後重試:%s", exc)
                await asyncio.sleep(3)
                continue

            if not data.get("ok"):
                log.error("getUpdates 回傳錯誤:%s", data)
                await asyncio.sleep(3)
                continue

            for update in data.get("result") or []:
                offset = update["update_id"] + 1
                for inbound in self.parse(update):
                    await self.show_typing(inbound)
                    reply = await handler(inbound)
                    if reply:
                        await self.send(inbound, reply)

    async def set_webhook(self, url: str) -> dict[str, Any]:
        payload: dict[str, Any] = {"url": url, "allowed_updates": ["message"]}
        if self.settings.telegram.webhook_secret:
            payload["secret_token"] = self.settings.telegram.webhook_secret
        response = await self.client.post(f"{self._api}/setWebhook", json=payload)
        return response.json()

    async def delete_webhook(self) -> dict[str, Any]:
        response = await self.client.post(f"{self._api}/deleteWebhook")
        return response.json()


def hmac_equal(a: str, b: str) -> bool:
    import hmac

    return hmac.compare_digest(a or "", b or "")


def _header(headers: Mapping[str, str], key: str) -> str:
    for k, v in headers.items():
        if k.lower() == key:
            return v
    return ""
