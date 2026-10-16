"""核心流程:一則訊息進來,一組回覆出去。跟平台完全無關。"""

from __future__ import annotations

import asyncio
import logging
import re

from .brain import Brain
from .config import Settings, get_settings
from .memory import Memory
from .persona import Persona
from .rate_limit import RateLimiter
from .store import Store
from .types import InboundMessage, Reply

log = logging.getLogger(__name__)

HELP_TEXT = (
    "可以用的指令:\n"
    "/reset - 讓我忘掉我們聊過的一切\n"
    "/whoami - 看我現在記得你什麼\n"
    "/persona - 看我是誰\n"
    "/help - 這份清單"
)


class Engine:
    def __init__(
        self,
        settings: Settings | None = None,
        persona: Persona | None = None,
        store: Store | None = None,
        brain: Brain | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.persona = persona or Persona.load(self.settings.persona_path)
        self.store = store or Store(self.settings.db_path)
        self.brain = brain or Brain(self.settings)
        self.memory = Memory(self.store, self.brain, self.settings)
        self.limiter = RateLimiter(self.settings.rate_limit_per_minute)
        self._background: set[asyncio.Task] = set()

    async def handle(self, inbound: InboundMessage) -> Reply:
        text = inbound.text.strip()
        if not text:
            return Reply([])

        if not self.limiter.allow(inbound.conversation_key):
            log.warning("擋下洗版:%s", inbound.conversation_key)
            return Reply(["欸等一下,你講太快了,我跟不上 🙃"])

        conv = self.store.get_or_create_conversation(
            platform=inbound.platform,
            user_id=inbound.user_id,
            persona_id=self.persona.id,
            display_name=inbound.display_name,
        )

        if text.startswith("/"):
            command_reply = self._handle_command(conv.id, text)
            if command_reply is not None:
                return command_reply

        self.store.add_message(conv.id, "user", text)
        conv = self.store.get_conversation(conv.id) or conv
        context = self.memory.build(conv)

        try:
            raw = await self.brain.chat(
                persona=self.persona,
                history=context.history,
                summary=context.summary,
                facts=context.facts,
                display_name=conv.display_name,
            )
        except Exception:  # 不管哪裡壞了,使用者都該收到點東西
            log.exception("產生回覆時出錯(conversation_id=%s)", conv.id)
            return Reply(["欸,我這邊好像出了點問題,等一下再跟我說一次?"])

        bubbles = split_bubbles(
            raw, self.settings.max_bubbles, long_bubble_chars=self.persona.max_chars_per_bubble * 2
        )
        if not bubbles:
            return Reply([])

        self.store.add_message(conv.id, "assistant", "\n\n".join(bubbles))
        self._spawn(self.memory.maintain(conv.id))
        return Reply(bubbles)

    def greeting(self) -> Reply:
        if not self.persona.greeting:
            return Reply([])
        return Reply(split_bubbles(self.persona.greeting, self.settings.max_bubbles))

    def _handle_command(self, conversation_id: int, text: str) -> Reply | None:
        command = text.split()[0].lower().lstrip("/")
        if command in {"start", "help"}:
            if command == "start":
                greeting = self.persona.greeting or ""
                return Reply([greeting, HELP_TEXT] if greeting else [HELP_TEXT])
            return Reply([HELP_TEXT])
        if command == "reset":
            self.store.reset_conversation(conversation_id)
            return Reply(["好,忘光了。我們重新開始?"])
        if command == "whoami":
            facts = self.store.get_facts(conversation_id)
            if not facts:
                return Reply(["我目前對你還沒什麼印象欸。"])
            return Reply(["我記得的:\n" + "\n".join(f"・{f}" for f in facts)])
        if command == "persona":
            bits = [f"{self.persona.name}"]
            if self.persona.one_liner:
                bits.append(self.persona.one_liner)
            return Reply(["\n".join(bits)])
        return None  # 不認得的斜線開頭就當成一般訊息

    def _spawn(self, coro) -> None:
        """背景跑記憶整理,並握住 task 參考避免被 GC 收走。"""
        try:
            task = asyncio.create_task(coro)
        except RuntimeError:  # 沒有 event loop(例如同步測試)
            coro.close()
            return
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def drain(self) -> None:
        """等背景的記憶整理跑完 —— 關機跟測試時用。"""
        while self._background:
            await asyncio.gather(*list(self._background), return_exceptions=True)


_SENTENCE_END = re.compile(r"(?<=[。!?!?~])\s*")


def split_bubbles(raw: str, max_bubbles: int, long_bubble_chars: int = 120) -> list[str]:
    """把模型回的一段文字拆成幾則訊息。

    先照空白行拆(這是我們要求模型用的分隔方式);如果模型沒照做又寫得很長,
    就退而求其次照句尾標點拆。
    """
    raw = raw.strip()
    if not raw:
        return []

    chunks = [c.strip() for c in re.split(r"\n\s*\n", raw) if c.strip()]
    if len(chunks) == 1 and len(chunks[0]) > long_bubble_chars:
        sentences = [s.strip() for s in _SENTENCE_END.split(chunks[0]) if s.strip()]
        if len(sentences) > 1:
            chunks = _regroup(sentences, max_bubbles)

    if len(chunks) > max_bubbles:
        head = chunks[: max_bubbles - 1]
        tail = "\n".join(chunks[max_bubbles - 1 :])
        chunks = [*head, tail]
    return chunks


def _regroup(sentences: list[str], max_bubbles: int) -> list[str]:
    """把句子平均分成最多 max_bubbles 組,別讓某一則特別長。"""
    groups: list[str] = []
    target = max(1, len(sentences) // max_bubbles + (1 if len(sentences) % max_bubbles else 0))
    for i in range(0, len(sentences), target):
        groups.append("".join(sentences[i : i + target]))
    return groups[:max_bubbles]
