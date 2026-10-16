"""跨平台共用的訊息型別:三個平台進來的東西都先轉成這個樣子。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class InboundMessage:
    """使用者送進來的一則訊息。"""

    platform: str  # line | messenger | telegram | cli
    user_id: str  # 平台內的使用者 ID
    text: str
    display_name: str | None = None
    reply_token: str | None = None  # LINE 專用,一分鐘內有效
    received_at: float = field(default_factory=time.time)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def conversation_key(self) -> str:
        return f"{self.platform}:{self.user_id}"


@dataclass
class Reply:
    """要回出去的內容,拆成數個泡泡比較像真人。"""

    bubbles: list[str]

    def __post_init__(self) -> None:
        self.bubbles = [b.strip() for b in self.bubbles if b and b.strip()]

    @property
    def text(self) -> str:
        return "\n\n".join(self.bubbles)

    def __bool__(self) -> bool:
        return bool(self.bubbles)
