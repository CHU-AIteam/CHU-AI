"""アプリ全体で共有する設定値の定義。

環境変数の読み取りとバリデーションを一元化し、
業務ロジックやI/O処理は持たない。
"""

from pathlib import Path

import math
import os


def get_bool_env(name: str, default: bool) -> bool:
    """真偽値環境変数を読む。未設定は既定値を返す。"""
    raw = os.getenv(name)
    if raw is None:
        return default

    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


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


def get_choice_env(name: str, default: str, valid_values: set[str]) -> str:
    """文字列環境変数を読む。無効値は既定値へ戻す。"""
    value = os.getenv(name, default).strip().lower()
    if value in valid_values:
        return value
    return default


GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
"""文章生成に使うGeminiモデル"""

ROUTER_ENABLED = get_bool_env("ROUTER_ENABLED", True)
"""事前分類ルーターを使うかどうか"""

ROUTER_MODEL = (
    os.getenv("ROUTER_MODEL", "gemini-3.1-flash-lite").strip()
    or "gemini-3.1-flash-lite"
)
"""RAG要否の事前分類に使うGeminiモデル"""

ROUTER_CONFIDENCE_THRESHOLD = min(
    get_non_negative_float_env("ROUTER_CONFIDENCE_THRESHOLD", 0.85),
    1.0,
)
"""RAG検索をスキップしてよいルーター信頼度の下限"""

KNOWLEDGE_INDEX_FILE = "99_knowledge_h2_summary_index.md"
"""ナレッジ選定に使うH2要約目次ファイル"""

KNOWLEDGE_TOP_K = int(os.getenv("KNOWLEDGE_TOP_K", "4"))
"""質問ごとに採用するナレッジファイル上限"""

KNOWLEDGE_MAX_CHARS = int(os.getenv("KNOWLEDGE_MAX_CHARS", "26000"))
"""質問ごとに採用するナレッジ本文の文字数上限"""

VALID_KNOWLEDGE_MODES = {"all", "search"}
"""利用可能なナレッジ投入モード"""

KNOWLEDGE_MODE_DEFAULT = get_choice_env(
    "KNOWLEDGE_MODE_DEFAULT", "search", VALID_KNOWLEDGE_MODES
)
"""knowledge_mode未指定時に使う既定モード"""

VALID_SEARCH_BACKENDS = {"legacy", "hybrid"}
"""利用可能な検索バックエンド"""

SEARCH_BACKEND_DEFAULT = get_choice_env(
    "SEARCH_BACKEND_DEFAULT",
    "legacy",
    VALID_SEARCH_BACKENDS,
)
"""knowledge_mode=search で使う検索バックエンド"""

VALID_LOG_STORAGE_MODES = {"postgres", "google_sheets"}
"""利用可能なチャットログ・フィードバック保存先"""

LOG_STORAGE_MODE = get_choice_env(
    "LOG_STORAGE_MODE",
    "postgres",
    VALID_LOG_STORAGE_MODES,
)
"""chat_logs / feedback_logs の保存先"""

POSTGRES_DSN = os.getenv("POSTGRES_DSN", "").strip()
"""ハイブリッド検索用PostgreSQL DSN"""

POSTGRES_CONNECT_TIMEOUT_SECONDS = get_positive_int_env(
    "POSTGRES_CONNECT_TIMEOUT_SECONDS",
    5,
)
"""PostgreSQL接続タイムアウト秒数"""

HYBRID_AUTO_INIT_DB = get_bool_env("HYBRID_AUTO_INIT_DB", True)
"""起動時にhybrid検索用テーブルを自動初期化するか"""

HYBRID_EMBEDDING_MODEL = os.getenv(
    "HYBRID_EMBEDDING_MODEL",
    "gemini-embedding-001",
).strip()
"""埋め込みに使うGeminiモデル"""

HYBRID_EMBEDDING_DIM = get_positive_int_env("HYBRID_EMBEDDING_DIM", 768)
"""埋め込みベクトル次元数"""

HYBRID_CHUNK_SIZE = get_positive_int_env("HYBRID_CHUNK_SIZE", 500)
"""インデックス作成時の1チャンクあたり文字数上限"""

HYBRID_CHUNK_OVERLAP = get_non_negative_int_env("HYBRID_CHUNK_OVERLAP", 80)
"""連続チャンク間の重複文字数"""

if HYBRID_CHUNK_OVERLAP >= HYBRID_CHUNK_SIZE:
    HYBRID_CHUNK_OVERLAP = max(HYBRID_CHUNK_SIZE // 4, 0)

HYBRID_KEYWORD_TOP_K = get_positive_int_env("HYBRID_KEYWORD_TOP_K", 20)
"""キーワード検索で取得する候補件数"""

HYBRID_VECTOR_TOP_K = get_positive_int_env("HYBRID_VECTOR_TOP_K", 20)
"""ベクトル検索で取得する候補件数"""

HYBRID_FINAL_TOP_K = get_positive_int_env("HYBRID_FINAL_TOP_K", 6)
"""RRF統合後に採用する最終候補件数"""

HYBRID_RRF_K = get_positive_int_env("HYBRID_RRF_K", 60)
"""RRF統合で使う安定化係数"""

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
"""チャットログを保存するかどうか"""

_chat_log_postgres_dsn = os.getenv("CHAT_LOG_POSTGRES_DSN")
CHAT_LOG_POSTGRES_DSN = (
    _chat_log_postgres_dsn.strip()
    if _chat_log_postgres_dsn and _chat_log_postgres_dsn.strip()
    else POSTGRES_DSN
)
"""チャットログ保存先のPostgreSQL DSN。未設定時は POSTGRES_DSN を流用する。"""

CHAT_LOG_ADMIN_API_KEY = os.getenv("CHAT_LOG_ADMIN_API_KEY", "").strip()
"""管理用チャットログAPIを呼ぶための固定キー。空なら閲覧APIを無効化する。"""

CHAT_LOG_LIST_DEFAULT_LIMIT = get_positive_int_env("CHAT_LOG_LIST_DEFAULT_LIMIT", 50)
"""チャットログ閲覧APIの既定件数。"""

CHAT_LOG_LIST_MAX_LIMIT = get_positive_int_env("CHAT_LOG_LIST_MAX_LIMIT", 200)
"""チャットログ閲覧APIで許可する最大取得件数。"""

if CHAT_LOG_LIST_MAX_LIMIT < CHAT_LOG_LIST_DEFAULT_LIMIT:
    CHAT_LOG_LIST_MAX_LIMIT = CHAT_LOG_LIST_DEFAULT_LIMIT

GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
"""Google Sheets APIで使うサービスアカウントJSONのパス"""

GOOGLE_SHEETS_SPREADSHEET_ID = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "").strip()
"""chat_logs / feedback_logs を保存するGoogleスプレッドシートID"""

GOOGLE_CHAT_LOG_SHEET_NAME = os.getenv("GOOGLE_CHAT_LOG_SHEET_NAME", "chat_logs").strip()
"""chat_logs を保存するシート名"""

GOOGLE_FEEDBACK_LOG_SHEET_NAME = os.getenv(
    "GOOGLE_FEEDBACK_LOG_SHEET_NAME",
    "feedback_logs",
).strip()
"""feedback_logs を保存するシート名"""

KNOWLEDGE_DIR = Path(__file__).resolve().parents[1] / "knowledge"
"""回答元情報を置くディレクトリ"""

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
"""チャットUIの静的ファイルディレクトリ"""

PROJECT_ROOT = Path(__file__).resolve().parents[3]
"""Chubu Commons AIリポジトリのルートディレクトリ"""
