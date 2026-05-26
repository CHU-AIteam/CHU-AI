from pathlib import Path

import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

from chu_ai.config import CHAT_LOG_DB_PATH, CHAT_LOG_ENABLED, PROJECT_ROOT
from chu_ai.prompts import AI_prompt, RESPONSE_JSON_INSTRUCTION, chara_personality


def compact_log_text(text: str, max_chars: int = 120) -> str:
    """ログ用に改行と長文を短く整える関数"""
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= max_chars:
        return compact
    return f"{compact[:max_chars]}..."


def extract_current_question_for_log(text: str) -> str:
    """履歴付きリクエストから現在の質問だけを取り出す関数"""
    marker = "【現在の質問】"
    if marker in text:
        return text.rsplit(marker, 1)[1].strip()
    return text.strip()


def count_history_entries_for_log(text: str) -> int:
    """履歴付きリクエスト内の過去会話件数を数える関数"""
    return len(re.findall(r"(?m)^\d+\.$", text))


def extract_conversation_history(text: str) -> list[dict[str, str]]:
    """履歴付きリクエストから過去会話の内容を取り出す関数"""
    history_marker = "【過去の会話履歴】"
    current_marker = "【現在の質問】"
    if history_marker not in text:
        return []

    history_section = text.split(history_marker, 1)[1]
    if current_marker in history_section:
        history_section = history_section.split(current_marker, 1)[0]

    conversations = []
    for chunk in re.split(r"(?m)^\d+\.\s*$", history_section):
        chunk = chunk.strip()
        if not chunk:
            continue

        match = re.search(r"ユーザー:\s*(.*?)\nChu-AI:\s*([\s\S]*)", chunk)
        if not match:
            continue

        conversations.append(
            {
                "user": match.group(1).strip(),
                "bot": match.group(2).strip(),
            }
        )

    return conversations


def format_used_files_for_log(used_files: list[str], preview_count: int = 5) -> str:
    """採用knowledge一覧をログ用に短く整える関数"""
    if not used_files:
        return "0 files"

    preview = ", ".join(used_files[:preview_count])
    remaining = len(used_files) - preview_count
    if remaining > 0:
        preview = f"{preview}, ... +{remaining} more"
    return f"{len(used_files)} files [{preview}]"


def build_gemini_prompt_for_log(user_text: str) -> str:
    """Geminiへ送るプロンプトをknowledgeだけ伏せてログ用に作る関数"""
    return f"""
【指示】
{AI_prompt}
【出力形式】
{RESPONSE_JSON_INSTRUCTION}
【性格】
{chara_personality}
【ナレッジ】
[知識]
【質問】
{user_text}
""".strip()


def current_asked_at() -> str:
    """DB保存用の日本時間タイムスタンプを返す関数"""
    jst = timezone(timedelta(hours=9))
    return datetime.now(jst).isoformat(timespec="seconds")


def resolve_chat_log_db_path() -> Path:
    """チャットログDBパスを絶対パスへ変換する関数"""
    db_path = Path(CHAT_LOG_DB_PATH).expanduser()
    if db_path.is_absolute():
        return db_path
    return PROJECT_ROOT / db_path


def initialize_chat_log_db() -> None:
    """SQLiteのチャットログテーブルを作成する関数"""
    if not CHAT_LOG_ENABLED:
        return

    db_path = resolve_chat_log_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asked_at TEXT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                can_answer INTEGER NOT NULL,
                used_knowledge_files_json TEXT NOT NULL,
                used_conversation_json TEXT NOT NULL,
                recommended_questions_json TEXT NOT NULL,
                knowledge_mode TEXT NOT NULL,
                request_text TEXT NOT NULL,
                error_type TEXT,
                error_message TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_chat_logs_asked_at
            ON chat_logs (asked_at)
            """
        )


def save_chat_log(
    *,
    asked_at: str,
    question: str,
    answer: str,
    can_answer: bool,
    used_knowledge_files: list[str],
    used_conversation: list[dict[str, str]],
    recommended_questions: list[str],
    knowledge_mode: str,
    request_text: str,
    error_type: str | None = None,
    error_message: str | None = None,
) -> None:
    """チャット1件分をSQLiteへ保存する関数"""
    if not CHAT_LOG_ENABLED:
        return

    try:
        initialize_chat_log_db()
        with sqlite3.connect(resolve_chat_log_db_path()) as connection:
            connection.execute(
                """
                INSERT INTO chat_logs (
                    asked_at,
                    question,
                    answer,
                    can_answer,
                    used_knowledge_files_json,
                    used_conversation_json,
                    recommended_questions_json,
                    knowledge_mode,
                    request_text,
                    error_type,
                    error_message
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asked_at,
                    question,
                    answer,
                    1 if can_answer else 0,
                    json.dumps(used_knowledge_files, ensure_ascii=False),
                    json.dumps(used_conversation, ensure_ascii=False),
                    json.dumps(recommended_questions, ensure_ascii=False),
                    knowledge_mode,
                    request_text,
                    error_type,
                    error_message,
                ),
            )
    except Exception as error:
        print(f"Chat log save failed: {error}", file=sys.stderr)
