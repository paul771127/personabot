"""接口的共同形狀。"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Mapping

import httpx

from ..config import Settings
from ..types import InboundMessage, Reply

log = logging.getLogger(__name__)


class Adapter(ABC):
    """一個平台的進出口:驗簽、把 payload 轉成 InboundMessage、把 Reply 送回去。"""

    name: str = "base"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._client = client
        self._owns_client = client is None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    @abstractmethod
    def verify(self, body: bytes, headers: Mapping[str, str]) -> bool:
        """驗證這個請求真的來自平台。驗不過就不要處理。"""

    @abstractmethod
    def parse(self, payload: dict[str, Any]) -> list[InboundMessage]:
        """從 webhook payload 取出使用者的文字訊息,其他型別一律忽略。"""

    @abstractmethod
    async def send(self, inbound: InboundMessage, reply: Reply) -> None:
        """把回覆送出去。"""

    async def show_typing(self, inbound: InboundMessage, seconds: float = 3.0) -> None:
        """顯示「輸入中」。平台不支援就當沒這回事。"""
        return None

    async def _post(self, url: str, *, json: dict, headers: dict | None = None) -> None:
        try:
            response = await self.client.post(url, json=json, headers=headers)
            if response.status_code >= 400:
                log.error("%s 送出失敗 %s:%s", self.name, response.status_code, response.text[:400])
        except httpx.HTTPError as exc:
            log.error("%s 送出失敗:%s", self.name, exc)


def typing_delay(text: str, settings: Settings) -> float:
    """照字數估一個打字時間,上限壓著,免得人家等太久。"""
    if settings.typing_speed_cps <= 0:
        return 0.0
    return min(len(text) / settings.typing_speed_cps, settings.max_typing_delay)


async def pace(settings: Settings, text: str) -> None:
    """送下一則泡泡之前先停一下,像在打字。"""
    delay = typing_delay(text, settings)
    if delay > 0:
        await asyncio.sleep(delay)
