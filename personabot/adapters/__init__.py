"""各平台的接口。"""

from .base import Adapter, pace
from .line import LineAdapter
from .messenger import MessengerAdapter
from .telegram import TelegramAdapter

__all__ = ["Adapter", "LineAdapter", "MessengerAdapter", "TelegramAdapter", "pace"]
