"""記憶:短期靠對話視窗,中期靠摘要,長期靠事實清單。"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from . import rapport
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
    def __init__(
        self, store: Store, brain: Brain, settings: Settings, assistant_name: str = "機器人"
    ) -> None:
        self.store = store
        self.brain = brain
        self.settings = settings
        # 抽記憶時要能明確區分誰是誰,否則小模型會把機器人自己的背景
        # 當成使用者的事記下來。
        self.assistant_name = assistant_name

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
            await self._update_rapport(conversation_id)
            await self._extract_facts(conversation_id)
            await self._maybe_summarize(conversation_id)
        except Exception:  # 背景整理壞掉不該害使用者收不到回覆
            log.exception("整理記憶時出錯(conversation_id=%s)", conversation_id)

    async def _update_rapport(self, conversation_id: int) -> None:
        """重算興趣度、戒心,並更新對方現在的狀態。

        程式那半(隔多久回、幾個字、是不是被逗笑)在 engine 收到訊息時就算好存著了;
        這裡補上只有模型看得出來的那半,然後把兩邊合起來。
        跑在回覆送出之後,所以影響的是下一輪。
        """
        if not self.settings.rapport:
            return
        conv = self.store.get_conversation(conversation_id)
        if conv is None:
            return
        recent = self.store.recent_messages(conversation_id, limit=4)
        last_user = next((m for m in reversed(recent) if m.role == "user"), None)
        if last_user is None:
            return

        signals = rapport.read_message(last_user.text, conv.last_gap, self.settings)
        flags, state = await self.brain.read_signals(recent, self.assistant_name)
        signals.asked_about_her = signals.asked_about_her or flags["asked_about_her"]
        signals.warm = signals.warm or flags["warm"]
        signals.probing = signals.probing or flags["probing"]
        signals.challenged = signals.challenged or flags["challenged"]

        interest = rapport.next_interest(conv.interest, signals, self.settings, last_user.text)
        guard, quiet = rapport.next_guard(conv.guard, conv.quiet_turns, signals, self.settings)
        his_messages = self.store.count_messages(conversation_id, role="user")
        stage = rapport.next_stage(conv.stage, his_messages, interest, guard)

        self.store.update_rapport(
            conversation_id,
            interest=interest,
            guard=guard,
            quiet_turns=quiet,
            read_state=state or conv.read_state,
            stage=stage,
        )
        if stage != conv.stage:
            log.info("conversation=%s 關係階段 %d->%d", conversation_id, conv.stage, stage)
        if (interest, guard) != (conv.interest, conv.guard):
            log.info(
                "conversation=%s 興趣度 %d->%d,戒心 %d->%d",
                conversation_id, conv.interest, interest, conv.guard, guard,
            )

    async def _extract_facts(self, conversation_id: int) -> None:
        conv = self.store.get_conversation(conversation_id)
        if conv is None:
            return
        recent = self.store.recent_messages(conversation_id, limit=6)
        if len(recent) < 2:
            return
        known = self.store.get_facts(conversation_id)
        facts = await self.brain.extract_facts(recent, known, self.assistant_name)
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

        summary = await self.brain.summarize(to_fold, conv.summary, self.assistant_name)
        self.store.set_summary(conversation_id, summary, cutoff)
        log.info("conversation=%s 已把 %d 則舊訊息壓成摘要", conversation_id, len(to_fold))

        # 趁整理的時候順手把長期記憶也收一收。寫入時擋掉的是「新的跟舊的重複」,
        # 但有些重複要累積一陣子才看得出來,這裡從頭掃一次。
        merged = self.store.compact_facts(conversation_id)
        if merged:
            log.info("conversation=%s 合併了 %d 則重複的長期記憶", conversation_id, merged)

        # 字面上併不掉的那種重複(「在台中念資工」vs「就讀資訊工程學系三年級」)
        # 只有模型分得出來。挑在這裡做:摘要本來就改寫了 system prompt 的後半段,
        # 後端的 prompt 快取這一輪反正要重算,順手把記憶也整理掉最不浪費。
        if self.settings.merge_facts:
            await self._merge_facts(conversation_id)

    async def _merge_facts(self, conversation_id: int) -> None:
        facts = self.store.get_facts(conversation_id)
        tidied = await self.brain.merge_facts(facts)
        if tidied == facts:
            return
        self.store.replace_facts(conversation_id, tidied)
        log.info(
            "conversation=%s 長期記憶從 %d 則整理成 %d 則", conversation_id, len(facts), len(tidied)
        )
