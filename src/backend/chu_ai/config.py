"""アプリ全体で共有する設定値の定義。

環境変数の読み取りとバリデーションを一元化し、
業務ロジックやI/O処理は持たない。
"""

from pathlib import Path

import math
import os


def get_positive_int_env(name: str, default: int) -> int:
    """正の整数環境変数を読む。未設定、不正値、0以下は既定値に戻す。"""
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def get_non_negative_int_env(name: str, default: int) -> int:
    """0以上の整数環境変数を読む。未設定、不正値、負数は既定値に戻す。"""
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value >= 0 else default


def get_non_negative_float_env(name: str, default: float) -> float:
    """0以上の数値環境変数を読む。未設定、不正値、負数は既定値に戻す。"""
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if math.isfinite(value) and value >= 0 else default


GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
"""文章生成に使うGeminiモデル"""

KNOWLEDGE_INDEX_FILE = "99_knowledge_h2_summary_index.md"
"""ナレッジ選定に使うH2要約目次ファイル"""

KNOWLEDGE_TOP_K = int(os.getenv("KNOWLEDGE_TOP_K", "4"))
"""質問ごとに採用するナレッジファイル上限"""

KNOWLEDGE_MAX_CHARS = int(os.getenv("KNOWLEDGE_MAX_CHARS", "26000"))
"""質問ごとに採用するナレッジ本文の文字数上限"""

VALID_KNOWLEDGE_MODES = {"all", "search"}
"""利用可能なナレッジ投入モード"""

KNOWLEDGE_MODE_DEFAULT = os.getenv("KNOWLEDGE_MODE_DEFAULT", "search").strip().lower()
"""knowledge_mode未指定時に使う既定モード"""

if KNOWLEDGE_MODE_DEFAULT not in VALID_KNOWLEDGE_MODES:
    KNOWLEDGE_MODE_DEFAULT = "search"

HOME_RETURN_SECONDS_DEFAULT = 30
"""チャット画面からホームへ戻るまでの既定秒数"""

HOME_RETURN_SECONDS = get_positive_int_env(
    "HOME_RETURN_SECONDS", HOME_RETURN_SECONDS_DEFAULT
)

CHAT_HISTORY_MAX_EXCHANGES_DEFAULT = 5
"""フロントエンドがGeminiへ同梱する過去会話の最大往復数"""

CHAT_HISTORY_WINDOW_MINUTES_DEFAULT = 2.0
"""フロントエンドがGeminiへ同梱する過去会話の保持分数"""

CHAT_HISTORY_MAX_EXCHANGES = get_non_negative_int_env(
    "CHAT_HISTORY_MAX_EXCHANGES",
    CHAT_HISTORY_MAX_EXCHANGES_DEFAULT,
)
"""フロントエンドへ渡す過去会話の最大往復数。0なら履歴を使わない。"""

CHAT_HISTORY_WINDOW_MINUTES = get_non_negative_float_env(
    "CHAT_HISTORY_WINDOW_MINUTES",
    CHAT_HISTORY_WINDOW_MINUTES_DEFAULT,
)
"""フロントエンドへ渡す過去会話の保持分数。0なら履歴を使わない。"""

CHAT_LOG_ENABLED = os.getenv("CHAT_LOG_ENABLED", "true").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
"""チャットログをSQLiteへ保存するかどうか"""

CHAT_LOG_DB_PATH = os.getenv("CHAT_LOG_DB_PATH", "data/chu_ai.sqlite3").strip()
"""チャットログSQLiteファイルのパス。相対パスはリポジトリルート基準。"""

KNOWLEDGE_DIR = Path(__file__).resolve().parents[1] / "knowledge"
"""回答元情報を置くディレクトリ"""

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
"""チャットUIの静的ファイルディレクトリ"""

PROJECT_ROOT = Path(__file__).resolve().parents[3]
"""CHU-AIリポジトリのルートディレクトリ"""
