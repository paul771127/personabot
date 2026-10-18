"""Facebook Messenger(Meta Send API)。

https://developers.facebook.com/docs/messenger-platform/
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from typing import Any

from ..types import InboundMessage, Reply
from .base import Adapter, header, lead_pause, pace

MAX_TEXT_LENGTH = 1950  # 官方上限 2000


class MessengerAdapter(Adapter):
    name = "messenger"

    @property
    def _send_url(self) -> str:
        cfg = self.settings.messenger
        return f"https://graph.facebook.com/{cfg.api_version}/me/messages?access_token={cfg.page_access_token}"

    def verify(self, body: bytes, headers: Mapping[str, str]) -> bool:
        secret = self.settings.messenger.app_secret
        signature = header(headers, "x-hub-signature-256")
        if not secret or not signature.startswith("sha256="):
            return False
        expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature[len("sha256=") :])

    def verify_subscription(self, mode: str, token: str) -> bool:
        """Meta 設定 webhook 時會先 GET 過來驗一次。"""
        return mode == "subscribe" and bool(token) and token == self.settings.messenger.verify_token

    def parse(self, payload: dict[str, Any]) -> list[InboundMessage]:
        out: list[InboundMessage] = []
        if payload.get("object") != "page":
            return out
        for entry in payload.get("entry") or []:
            for event in entry.get("messaging") or []:
                message = event.get("message") or {}
                # is_echo 是粉專自己送出去的訊息會回彈的那一份,不能當成使用者輸入。
                if message.get("is_echo"):
                    continue
                text = message.get("text")
                sender_id = (event.get("sender") or {}).get("id")
                if not text or not sender_id:
                    continue
                out.append(
                    InboundMessage(
                        platform=self.name,
                        user_id=sender_id,
                        text=text,
                        raw=event,
                    )
                )
        return out

    async def send(self, inbound: InboundMessage, reply: Reply) -> None:
        await lead_pause(reply)
        for index, bubble in enumerate(reply.bubbles):
            # 等過之後一定要再顯示一次「輸入中」。靜默完直接跳出訊息很突兀,
            # 而且等待期間對方看不到任何動靜,會以為機器人掛了。
            if index > 0 or reply.lead_delay > 0:
                await self.show_typing(inbound)
                await pace(self.settings, bubble)
            await self._post(
                self._send_url,
                json={
                    "recipient": {"id": inbound.user_id},
                    "messaging_type": "RESPONSE",
                    "message": {"text": bubble[:MAX_TEXT_LENGTH]},
                },
            )

    async def show_typing(self, inbound: InboundMessage, seconds: float = 3.0) -> None:
        if not self.settings.typing_indicator:
            return
        await self._post(
            self._send_url,
            json={"recipient": {"id": inbound.user_id}, "sender_action": "typing_on"},
        )
