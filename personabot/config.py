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


def _default_window_messages() -> int:
    # 小模型的有效上下文比標稱的短,帶太多反而開始失憶、出戲。
    return _int("WINDOW_MESSAGES", 16 if _backend() == "local" else 24)


def _default_summarize_after() -> int:
    return _int("SUMMARIZE_AFTER", 30 if _backend() == "local" else 40)


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
    # Qwen3 這類模型預設會先思考一大段,陪聊根本不需要,而且常常 token 全花在
    # 想、一個字都還沒答出來就被 max_tokens 截斷(實測 51 秒 vs 2 秒)。
    # 伺服器不認得這個參數的話會自動重送一次不帶它的請求。
    disable_thinking: bool = field(default_factory=lambda: _bool("LOCAL_DISABLE_THINKING", True))


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
    window_messages: int = field(default_factory=_default_window_messages)
    summarize_after: int = field(default_factory=_default_summarize_after)
    max_facts: int = field(default_factory=lambda: _int("MAX_FACTS", 60))
    # 摘要的時候多打一次 utility 模型,把講同一件事的記憶併起來。
    # 字面上的去重一直都會做,這個管的是語意上的那層。
    merge_facts: bool = field(default_factory=lambda: _bool("MERGE_FACTS", True))

    # 關係狀態:興趣度、戒心、對方狀態、回訊節奏
    rapport: bool = field(default_factory=lambda: _bool("RAPPORT", True))
    # 多久以內回算「秒回」,多久以上算「晾著」
    fast_reply_seconds: float = field(default_factory=lambda: _float("FAST_REPLY_SECONDS", 30.0))
    slow_reply_seconds: float = field(
        default_factory=lambda: _float("SLOW_REPLY_SECONDS", 6 * 3600.0)
    )
    # 幾個字以內算敷衍(「哈哈」這種被逗笑的不算)
    cold_reply_chars: int = field(default_factory=lambda: _int("COLD_REPLY_CHARS", 8))
    # 連續幾則沒再試探,戒心就降一格
    guard_cooldown_turns: int = field(default_factory=lambda: _int("GUARD_COOLDOWN_TURNS", 10))
    # 跟著對方的節奏回,但最多讓他等這麼久(秒)。
    # 不要調太大:對方剛送出訊息、正看著螢幕等回覆,超過半分鐘的靜默看起來
    # 不像矜持,像當機。實測 90 秒的時候會以為機器人掛了。
    max_lead_delay: float = field(default_factory=lambda: _float("MAX_LEAD_DELAY", 25.0))

    # 語氣收斂。提示詞裡寫「很少用表情符號」沒用,實測十二輪有八輪都帶表情,
    # 看起來很油。所以由程式把關:最近幾則用過表情,這則就不用。0 = 不限制。
    emoji_gap: int = field(default_factory=lambda: _int("EMOJI_GAP", 5))
    # 拉長音的波浪號幾乎一定讀起來像在撒嬌,預設拿掉。
    strip_tilde: bool = field(default_factory=lambda: _bool("STRIP_TILDE", True))
    # 讓她知道現在幾點、今天幾號、星期幾,話才對得上時間(半夜別說早安)。
    clock: bool = field(default_factory=lambda: _bool("CLOCK", True))

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
