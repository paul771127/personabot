"""核心流程:一則訊息進來,一組回覆出去。跟平台完全無關。"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime

from . import clock, rapport
from .brain import Brain
from .bubbles import split_bubbles, tone_down
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
        self.memory = Memory(self.store, self.brain, self.settings, self.persona.name)
        self.limiter = RateLimiter(self.settings.rate_limit_per_minute)
        self._background: set[asyncio.Task] = set()
        # 每輪回幾則是抽籤決定的。測試可以換成固定種子。
        self.rng = random.Random()

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

        started = time.monotonic()
        signals = self._read_pace(conv.id, inbound, text)
        self.store.add_message(conv.id, "user", text)
        self.store.update_rapport(
            conv.id, last_gap=signals.gap_seconds, last_chars=signals.chars
        )
        conv = self.store.get_conversation(conv.id) or conv
        context = self.memory.build(conv)
        budget = rapport.pick_bubbles(conv, signals, self.settings, self.rng)
        energy = rapport.pick_energy(conv, signals, self.rng, prev=conv.last_energy)
        self.store.update_rapport(conv.id, last_energy=energy)

        try:
            raw = await self.brain.chat(
                persona=self.persona,
                history=context.history,
                summary=context.summary,
                facts=context.facts,
                display_name=conv.display_name,
                rapport=rapport.describe(
                    conv,
                    self.settings,
                    signals,
                    budget,
                    strengths=self.persona.strengths,
                    energy=energy,
                    goal=self.persona.goal or None,
                ),
                max_bubbles=budget,
                now=clock.describe_now(datetime.now()) if self.settings.clock else "",
            )
        except Exception:  # 不管哪裡壞了,使用者都該收到點東西
            log.exception("產生回覆時出錯(conversation_id=%s)", conv.id)
            return Reply(["欸,我這邊好像出了點問題,等一下再跟我說一次?"])

        bubbles = split_bubbles(
            raw,
            budget,
            long_bubble_chars=self.persona.max_chars_per_bubble * 2,
            max_chars=self.persona.max_chars_per_bubble,
        )
        # 要在這則寫進資料庫之前看,不然會拿自己跟自己比。
        her_recent = [
            m.text for m in self.store.recent_messages(conv.id, limit=12) if m.role == "assistant"
        ]
        bubbles = tone_down(
            bubbles,
            her_recent,
            emoji_gap=self.settings.emoji_gap,
            strip_tilde=self.settings.strip_tilde,
        )
        if not bubbles:
            return Reply([])

        self.store.add_message(conv.id, "assistant", "\n\n".join(bubbles))
        self._spawn(self.memory.maintain(conv.id))
        # 想讓他等的總時間,扣掉模型已經跑掉的。模型想了八秒,就只要再等兩秒。
        target = rapport.lead_delay(conv, signals, self.settings)
        waited = time.monotonic() - started
        return Reply(bubbles, lead_delay=max(0.0, target - waited))

    def _read_pace(
        self, conversation_id: int, inbound: InboundMessage, text: str
    ) -> rapport.Signals:
        """對方隔多久才回、這則多長。要在寫進資料庫之前算,不然會跟自己比。"""
        last = self.store.last_message(conversation_id, "assistant")
        gap = inbound.received_at - last.created_at if last else 0.0
        return rapport.read_message(text, gap, self.settings)

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
