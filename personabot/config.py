"""設定:全部從環境變數讀,缺的就留空,用到的時候才報錯。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _int(key: str, default: int) -> int:
    raw = _env(key)
    return int(raw) if raw else default


def _float(key: str, default: float) -> float:
    raw = _env(key)
    return float(raw) if raw else default


def _bool(key: str, default: bool) -> bool:
    raw = _env(key).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class LineConfig:
    channel_secret: str = field(default_factory=lambda: _env("LINE_CHANNEL_SECRET"))
    access_token: str = field(default_factory=lambda: _env("LINE_CHANNEL_ACCESS_TOKEN"))

    @property
    def enabled(self) -> bool:
        return bool(self.channel_secret and self.access_token)


@dataclass(frozen=True)
class MessengerConfig:
    app_secret: str = field(default_factory=lambda: _env("MESSENGER_APP_SECRET"))
    page_access_token: str = field(default_factory=lambda: _env("MESSENGER_PAGE_ACCESS_TOKEN"))
    verify_token: str = field(default_factory=lambda: _env("MESSENGER_VERIFY_TOKEN"))
    api_version: str = field(default_factory=lambda: _env("MESSENGER_API_VERSION", "v21.0"))

    @property
    def enabled(self) -> bool:
        return bool(self.app_secret and self.page_access_token and self.verify_token)


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    # 設 webhook 時一起帶上的 secret_token,Telegram 會放在
    # X-Telegram-Bot-Api-Secret-Token header 裡回送。
    webhook_secret: str = field(default_factory=lambda: _env("TELEGRAM_WEBHOOK_SECRET"))

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token)


@dataclass(frozen=True)
class Settings:
    persona_path: Path = field(
        default_factory=lambda: Path(_env("PERSONA_PATH", "personas/aoi.yaml"))
    )
    db_path: Path = field(default_factory=lambda: Path(_env("DB_PATH", "data/personabot.db")))

    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    chat_model: str = field(default_factory=lambda: _env("CHAT_MODEL", "claude-opus-5-5"))
    # 摘要、抽取記憶這種背景雜事用便宜的模型就夠。
    utility_model: str = field(default_factory=lambda: _env("UTILITY_MODEL", "claude-haiku-5-5"))
    chat_effort: str = field(default_factory=lambda: _env("CHAT_EFFORT", "low"))
    max_tokens: int = field(default_factory=lambda: _int("MAX_TOKENS", 1024))

    # 記憶
    window_messages: int = field(default_factory=lambda: _int("WINDOW_MESSAGES", 24))
    summarize_after: int = field(default_factory=lambda: _int("SUMMARIZE_AFTER", 40))
    max_facts: int = field(default_factory=lambda: _int("MAX_FACTS", 60))

    # 擬真:拆泡泡、打字延遲
    max_bubbles: int = field(default_factory=lambda: _int("MAX_BUBBLES", 3))
    typing_indicator: bool = field(default_factory=lambda: _bool("TYPING_INDICATOR", True))
    typing_speed_cps: float = field(default_factory=lambda: _float("TYPING_SPEED_CPS", 18.0))
    max_typing_delay: float = field(default_factory=lambda: _float("MAX_TYPING_DELAY", 3.5))

    # 防呆:同一個人短時間連續丟訊息的上限
    rate_limit_per_minute: int = field(default_factory=lambda: _int("RATE_LIMIT_PER_MINUTE", 20))

    line: LineConfig = field(default_factory=LineConfig)
    messenger: MessengerConfig = field(default_factory=MessengerConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)


_settings: Settings | None = None


def get_settings() -> Settings:
    """整個程式共用一份設定。"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """測試用:讓下一次 get_settings() 重新讀環境變數。"""
    global _settings
    _settings = None


def missing_credentials() -> str:
    """沒有可用的模型憑證時回傳一段人話,有的話回空字串。"""
    if _env("ANTHROPIC_API_KEY") or _env("ANTHROPIC_AUTH_TOKEN"):
        return ""
    return (
        "找不到 ANTHROPIC_API_KEY。\n"
        "請複製 .env.example 成 .env,把金鑰填進去;"
        "金鑰在 https://console.anthropic.com/settings/keys 申請。"
    )
