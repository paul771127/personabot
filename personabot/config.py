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


def _backend() -> str:
    """anthropic(預設)或 local(Ollama 等 OpenAI 相容伺服器)。"""
    value = _env("LLM_BACKEND", "anthropic").lower()
    return value if value in {"anthropic", "local"} else "anthropic"


def _default_chat_model() -> str:
    fallback = "qwen3.5:9b-q8_0" if _backend() == "local" else "claude-opus-5-5"
    return _env("CHAT_MODEL", fallback)


def _default_utility_model() -> str:
    fallback = "qwen3.5:2b" if _backend() == "local" else "claude-haiku-5-5"
    return _env("UTILITY_MODEL", fallback)


@dataclass(frozen=True)
class LocalConfig:
    """本地模型伺服器。Ollama、LM Studio、llama.cpp、vLLM 都講 OpenAI 那套。"""

    base_url: str = field(
        default_factory=lambda: _env("LOCAL_BASE_URL", "http://localhost:11434/v1").rstrip("/")
    )
    # Ollama 不檢查金鑰,但有些伺服器會要,所以還是帶上。
    api_key: str = field(default_factory=lambda: _env("LOCAL_API_KEY", "ollama"))
    # 本地模型慢,預設給兩分鐘。
    timeout: float = field(default_factory=lambda: _float("LOCAL_TIMEOUT", 120.0))


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

    backend: str = field(default_factory=_backend)
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    chat_model: str = field(default_factory=_default_chat_model)
    # 摘要、抽取記憶這種背景雜事用小模型就夠。
    utility_model: str = field(default_factory=_default_utility_model)
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

    local: LocalConfig = field(default_factory=LocalConfig)
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


def missing_credentials(settings: Settings | None = None) -> str:
    """沒有可用的模型憑證時回傳一段人話,有的話回空字串。"""
    settings = settings or get_settings()
    if settings.backend == "local":
        return ""  # 本地伺服器不用金鑰;連不連得上由 --selftest 驗
    if _env("ANTHROPIC_API_KEY") or _env("ANTHROPIC_AUTH_TOKEN"):
        return ""
    return (
        "找不到 ANTHROPIC_API_KEY。\n"
        "請複製 .env.example 成 .env,把金鑰填進去;"
        "金鑰在 https://console.anthropic.com/settings/keys 申請。"
    )
