"""LINE Messaging API。

https://developers.line.biz/en/reference/messaging-api/

回覆一律走 reply API(免費、且一次最多可帶五則訊息),所以泡泡之間不另外插延遲 ——
LINE 本來就是一次把幾則訊息一起推給使用者。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from collections.abc import Mapping
from typing import Any

import httpx

from ..types import InboundMessage, Reply
from .base import Adapter, header, lead_pause

log = logging.getLogger(__name__)

API = "https://api.line.me/v2/bot"
MAX_MESSAGES_PER_REPLY = 5
MAX_TEXT_LENGTH = 4900  # 官方上限 5000,留一點餘裕


class LineAdapter(Adapter):
    name = "line"

    @property
    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.settings.line.access_token}"}

    def verify(self, body: bytes, headers: Mapping[str, str]) -> bool:
        secret = self.settings.line.channel_secret
        signature = header(headers, "x-line-signature")
        if not secret or not signature:
            return False
        digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
        expected = base64.b64encode(digest).decode("utf-8")
        return hmac.compare_digest(expected, signature)

    def parse(self, payload: dict[str, Any]) -> list[InboundMessage]:
        out: list[InboundMessage] = []
        for event in payload.get("events") or []:
            if event.get("type") != "message":
                continue
            message = event.get("message") or {}
            if message.get("type") != "text":
                continue
            source = event.get("source") or {}
            # 只處理一對一。群組要支援的話得另外想被 @ 到才回的規則。
            if source.get("type") != "user":
                continue
            user_id = source.get("userId")
            if not user_id:
                continue
            out.append(
                InboundMessage(
                    platform=self.name,
                    user_id=user_id,
                    text=message.get("text", ""),
                    reply_token=event.get("replyToken"),
                    raw=event,
                )
            )
        return out

    async def send(self, inbound: InboundMessage, reply: Reply) -> None:
        await lead_pause(reply, cap=30.0)
        messages = [
            {"type": "text", "text": b[:MAX_TEXT_LENGTH]}
            for b in reply.bubbles[:MAX_MESSAGES_PER_REPLY]
        ]
        if not messages:
            return
        if inbound.reply_token:
            await self._post(
                f"{API}/message/reply",
                json={"replyToken": inbound.reply_token, "messages": messages},
                headers=self._auth_headers,
            )
        else:
            # 沒有 reply token(例如主動問候)只能用 push,這會計入推播則數。
            await self._post(
                f"{API}/message/push",
                json={"to": inbound.user_id, "messages": messages},
                headers=self._auth_headers,
            )

    async def show_typing(self, inbound: InboundMessage, seconds: float = 3.0) -> None:
        if not self.settings.typing_indicator:
            return
        # loadingSeconds 必須是 5 的倍數,介於 5 到 60。
        loading = max(5, min(60, round(seconds / 5) * 5))
        await self._post(
            f"{API}/chat/loading/start",
            json={"chatId": inbound.user_id, "loadingSeconds": loading},
            headers=self._auth_headers,
        )

    async def fetch_display_name(self, user_id: str) -> str | None:
        try:
            response = await self.client.get(
                f"{API}/profile/{user_id}", headers=self._auth_headers
            )
            if response.status_code == 200:
                return response.json().get("displayName")
            log.warning("抓 LINE 使用者名稱失敗 %s:%s", response.status_code, response.text[:200])
        except httpx.HTTPError as exc:
            log.warning("抓 LINE 使用者名稱失敗:%s", exc)
        return None
