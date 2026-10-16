"""記憶:短期靠對話視窗,中期靠摘要,長期靠事實清單。"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .brain import Brain
from .config import Settings
from .store import Conversation, Message, Store

log = logging.getLogger(__name__)


@dataclass
class Context:
    history: list[Message]
    summary: str
    facts: list[str]


class Memory:
    def __init__(self, store: Store, brain: Brain, settings: Settings) -> None:
        self.store = store
        self.brain = brain
        self.settings = settings

    def build(self, conv: Conversation) -> Context:
        """組出這次要餵給模型的全部記憶。"""
        history = self.store.recent_messages(
            conv.id, limit=self.settings.window_messages, after_id=conv.summarized_upto
        )
        if not history:
            # 已摘要的部分之後還是要給模型一點上下文,至少拿最近幾則。
            history = self.store.recent_messages(conv.id, limit=self.settings.window_messages)
        return Context(
            history=history,
            summary=conv.summary,
            facts=self.store.get_facts(conv.id),
        )

    async def maintain(self, conversation_id: int) -> None:
        """每輪對話之後在背景跑:抽長期記憶,必要時把舊對話壓成摘要。

        這裡不讓例外往外跑 —— 記憶整理壞掉不該害使用者收不到回覆。
        """
        try:
            await self._extract_facts(conversation_id)
            await self._maybe_summarize(conversation_id)
        except Exception:  # 背景整理壞掉不該害使用者收不到回覆
            log.exception("整理記憶時出錯(conversation_id=%s)", conversation_id)

    async def _extract_facts(self, conversation_id: int) -> None:
        conv = self.store.get_conversation(conversation_id)
        if conv is None:
            return
        recent = self.store.recent_messages(conversation_id, limit=6)
        if len(recent) < 2:
            return
        known = self.store.get_facts(conversation_id)
        facts = await self.brain.extract_facts(recent, known)
        if facts:
            added = self.store.add_facts(conversation_id, facts, cap=self.settings.max_facts)
            if added:
                log.info("conversation=%s 新增 %d 則長期記憶", conversation_id, added)

    async def _maybe_summarize(self, conversation_id: int) -> None:
        conv = self.store.get_conversation(conversation_id)
        if conv is None:
            return
        unsummarized = self.store.count_messages(conversation_id, after_id=conv.summarized_upto)
        if unsummarized < self.settings.summarize_after:
            return

        # 保留最近一個視窗不摘要,其餘壓進筆記。
        keep = self.settings.window_messages
        window = self.store.recent_messages(conversation_id, limit=keep, after_id=conv.summarized_upto)
        if not window:
            return
        cutoff = window[0].id - 1
        to_fold = self.store.messages_between(conversation_id, conv.summarized_upto, cutoff)
        if not to_fold:
            return

        summary = await self.brain.summarize(to_fold, conv.summary)
        self.store.set_summary(conversation_id, summary, cutoff)
        log.info("conversation=%s 已把 %d 則舊訊息壓成摘要", conversation_id, len(to_fold))
