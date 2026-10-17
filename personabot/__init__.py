"""personabot:跨平台 AI 角色陪聊機器人。"""

import sys

# requirements.txt 沒辦法表達 Python 版本需求,所以太舊的 Python 會先在 pip
# 那邊炸成一串看不懂的「找不到符合的版本」。這裡至少讓執行時講人話。
if sys.version_info < (3, 10):
    raise SystemExit(
        "personabot 需要 Python 3.10 以上,你現在是 "
        "%d.%d。\n"
        "anthropic 套件從 1.0 起就要求 3.10,所以舊版 Python 連裝都裝不起來。\n"
        "到 https://www.python.org/downloads/ 裝一個新的,然後重建虛擬環境:\n"
        "    Windows      :  py -3.12 -m venv .venv\n"
        "    Linux/macOS  :  python3.12 -m venv .venv"
        % (sys.version_info[0], sys.version_info[1])
    )

__version__ = "0.1.0"
