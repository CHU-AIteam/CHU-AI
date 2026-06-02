"""チャットログの整形と永続化を担当するサービス。

履歴テキストの抽出補助とPostgreSQL保存を提供し、
回答生成の意思決定は行わない。
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone

from chu_ai.config import (
    CHAT_LOG_ENABLED,
    CHAT_LOG_POSTGRES_DSN,
    POSTGRES_CONNECT_TIMEOUT_SECONDS,
)
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

        match = re.search(r"ユーザー:\s*(.*?)\n(?:コモ|Chu-AI):\s*([\s\S]*)", chunk)
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


def has_chat_log_database_config() -> bool:
    """チャットログ保存用のPostgreSQL接続情報が設定されているか返す。"""
    return bool(CHAT_LOG_POSTGRES_DSN)


def get_chat_log_storage_label() -> str:
    """起動ログやヘルスチェック向けの保存先ラベルを返す。"""
    return "postgres"


def _connect_chat_log_postgres():
    """チャットログ保存先のPostgreSQL接続を返す。"""
    try:
        import psycopg
    except ImportError as error:
        raise RuntimeError(
            "psycopg が見つかりません。`pip install -r src/backend/requirements.txt` を実行してください。"
        ) from error

    if not CHAT_LOG_POSTGRES_DSN:
        raise RuntimeError("CHAT_LOG_POSTGRES_DSN が未設定です。")

    return psycopg.connect(
        CHAT_LOG_POSTGRES_DSN,
        connect_timeout=POSTGRES_CONNECT_TIMEOUT_SECONDS,
    )


def initialize_chat_log_db() -> None:
    """PostgreSQLのチャットログテーブルを作成する関数"""
    if not CHAT_LOG_ENABLED or not has_chat_log_database_config():
        return

    with _connect_chat_log_postgres() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_logs (
                    id BIGSERIAL PRIMARY KEY,
                    asked_at TIMESTAMPTZ NOT NULL,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    can_answer BOOLEAN NOT NULL,
                    used_knowledge_files_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                    used_conversation_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                    recommended_questions_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                    knowledge_mode TEXT NOT NULL,
                    request_text TEXT NOT NULL,
                    error_type TEXT,
                    error_message TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chat_logs_asked_at
                ON chat_logs (asked_at DESC, id DESC)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chat_logs_can_answer
                ON chat_logs (can_answer)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chat_logs_knowledge_mode
                ON chat_logs (knowledge_mode)
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
) -> int | None:
    """チャット1件分をPostgreSQLへ保存する関数"""
    if not CHAT_LOG_ENABLED:
        return None

    if not has_chat_log_database_config():
        print("Chat log save skipped: CHAT_LOG_POSTGRES_DSN is not configured.", file=sys.stderr)
        return None

    try:
        from psycopg.types.json import Jsonb
    except ImportError as error:
        print(f"Chat log save failed: {error}", file=sys.stderr)
        return None

    try:
        initialize_chat_log_db()
        asked_at_value = datetime.fromisoformat(asked_at)
        with _connect_chat_log_postgres() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
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
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        asked_at_value,
                        question,
                        answer,
                        can_answer,
                        Jsonb(used_knowledge_files),
                        Jsonb(used_conversation),
                        Jsonb(recommended_questions),
                        knowledge_mode,
                        request_text,
                        error_type,
                        error_message,
                    ),
                )
                row = cursor.fetchone()
                if row:
                    return int(row[0])
    except Exception as error:
        print(f"Chat log save failed: {error}", file=sys.stderr)
    return None


def list_chat_logs(
    *,
    limit: int,
    offset: int = 0,
    can_answer: bool | None = None,
    knowledge_mode: str | None = None,
    query_text: str | None = None,
) -> tuple[int, list[dict]]:
    """チャットログを新しい順で取得する。"""
    if not CHAT_LOG_ENABLED:
        return 0, []
    if not has_chat_log_database_config():
        raise RuntimeError("CHAT_LOG_POSTGRES_DSN が未設定のため chat_logs を参照できません。")
    initialize_chat_log_db()

    try:
        from psycopg.rows import dict_row
    except ImportError as error:
        raise RuntimeError(
            "psycopg が見つかりません。`pip install -r src/backend/requirements.txt` を実行してください。"
        ) from error

    conditions: list[str] = []
    params: list[object] = []

    if can_answer is not None:
        conditions.append("can_answer = %s")
        params.append(can_answer)

    normalized_mode = (knowledge_mode or "").strip().lower()
    if normalized_mode:
        conditions.append("knowledge_mode = %s")
        params.append(normalized_mode)

    normalized_query = (query_text or "").strip()
    if normalized_query:
        conditions.append("(question ILIKE %s OR answer ILIKE %s)")
        like = f"%{normalized_query}%"
        params.extend([like, like])

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    with _connect_chat_log_postgres() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                f"SELECT COUNT(*) AS total FROM chat_logs {where_clause}",
                params,
            )
            total = int(cursor.fetchone()["total"])

            cursor.execute(
                f"""
                SELECT
                    id,
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
                FROM chat_logs
                {where_clause}
                ORDER BY asked_at DESC, id DESC
                LIMIT %s
                OFFSET %s
                """,
                [*params, limit, offset],
            )
            rows = cursor.fetchall()

    items = []
    for row in rows:
        asked_at_value = row["asked_at"]
        asked_at_text = asked_at_value.astimezone(
            timezone(timedelta(hours=9))
        ).isoformat(timespec="seconds")
        items.append(
            {
                "id": int(row["id"]),
                "asked_at": asked_at_text,
                "question": row["question"],
                "answer": row["answer"],
                "can_answer": bool(row["can_answer"]),
                "used_files": list(row["used_knowledge_files_json"] or []),
                "used_conversation": list(row["used_conversation_json"] or []),
                "recommended_questions": list(row["recommended_questions_json"] or []),
                "knowledge_mode": row["knowledge_mode"],
                "request_text": row["request_text"],
                "error_type": row["error_type"],
                "error_message": row["error_message"],
            }
        )

    return total, items
