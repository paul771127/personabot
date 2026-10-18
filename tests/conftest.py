from __future__ import annotations

import os

import pytest

from personabot.config import Settings, reset_settings
from personabot.persona import Persona
from personabot.store import Message, Store

# personabot.config 在 import 時會 load_dotenv(),所以開發者自己的 .env 會流進
# os.environ,測試結果就跟著那台機器的設定跑。每個測試前清掉,要什麼自己設。
_ENV_PREFIXES = (
    "LLM_BACKEND", "ANTHROPIC_", "CHAT_", "UTILITY_", "MAX_", "PERSONA_", "DB_PATH",
    "WINDOW_", "SUMMARIZE_", "TYPING_", "RATE_LIMIT_", "LOCAL_", "MERGE_", "RAPPORT", "FAST_REPLY_", "SLOW_REPLY_",
    "COLD_REPLY_", "GUARD_", "MAX_LEAD_", "EMOJI_", "STRIP_TILDE", "CLOCK",
    "LINE_", "MESSENGER_", "TELEGRAM_",
)


@pytest.fixture(autouse=True)
def _clean_settings(monkeypatch):
    for key in list(os.environ):
        if key.startswith(_ENV_PREFIXES):
            monkeypatch.delenv(key, raising=False)
    reset_settings()
    yield
    reset_settings()


@pytest.fixture
def persona() -> Persona:
    return Persona.from_dict(
        {
            "id": "test",
            "name": "小葵",
            "one_liner": "書籍編輯。",
            "personality": ["話不多"],
            "speech_style": ["短句"],
            "greeting": "欸,你好。",
        }
    )


@pytest.fixture
def store() -> Store:
    return Store(":memory:")


class FakeBrain:
    """不打 API 的假腦袋,照 queue 吐回覆並記下每次收到什麼。"""

    def __init__(self, replies: list[str] | None = None) -> None:
        self.replies = list(replies or ["嗯。"])
        self.chat_calls: list[dict] = []
        self.facts_to_return: list[str] = []
        self.last_assistant_name: str | None = None
        self.summary_to_return = "摘要"
        # None = 不動它,照傳進來的原樣回去。
        self.merged_facts_to_return: list[str] | None = None
        self.merge_calls: list[list[str]] = []
        self.signals_to_return: tuple[dict, str] = (
            {"asked_about_her": False, "warm": False, "probing": False, "challenged": False},
            "",
        )
        self.signal_calls: list[list[Message]] = []

    async def chat(
        self,
        persona,
        history,
        summary="",
        facts=None,
        display_name=None,
        rapport="",
        max_bubbles=None,
        now="",
    ):
        self.chat_calls.append(
            {
                "history": list(history),
                "summary": summary,
                "facts": list(facts or []),
                "display_name": display_name,
                "rapport": rapport,
                "max_bubbles": max_bubbles,
                "now": now,
            }
        )
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]

    async def extract_facts(
        self, messages: list[Message], known: list[str], assistant_name: str = "機器人"
    ) -> list[str]:
        self.last_assistant_name = assistant_name
        return list(self.facts_to_return)

    async def summarize(
        self, messages: list[Message], previous_summary: str = "", assistant_name: str = "機器人"
    ) -> str:
        self.last_assistant_name = assistant_name
        return self.summary_to_return

    async def read_signals(
        self, messages: list[Message], assistant_name: str = "機器人"
    ) -> tuple[dict, str]:
        self.signal_calls.append(list(messages))
        flags, state = self.signals_to_return
        return dict(flags), state

    async def merge_facts(self, facts: list[str]) -> list[str]:
        self.merge_calls.append(list(facts))
        if self.merged_facts_to_return is None:
            return list(facts)
        return list(self.merged_facts_to_return)


@pytest.fixture
def brain() -> FakeBrain:
    return FakeBrain()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        persona_path=tmp_path / "persona.yaml",
        db_path=":memory:",
        anthropic_api_key="test-key",
    )
