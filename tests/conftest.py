from __future__ import annotations

import pytest

from personabot.config import Settings, reset_settings
from personabot.persona import Persona
from personabot.store import Message, Store


@pytest.fixture(autouse=True)
def _clean_settings():
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
        self.summary_to_return = "摘要"

    async def chat(self, persona, history, summary="", facts=None, display_name=None):
        self.chat_calls.append(
            {
                "history": list(history),
                "summary": summary,
                "facts": list(facts or []),
                "display_name": display_name,
            }
        )
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]

    async def extract_facts(self, messages: list[Message], known: list[str]) -> list[str]:
        return list(self.facts_to_return)

    async def summarize(self, messages: list[Message], previous_summary: str = "") -> str:
        return self.summary_to_return


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
