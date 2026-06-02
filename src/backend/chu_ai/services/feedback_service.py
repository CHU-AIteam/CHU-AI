"""チャット回答へのフィードバック保存と参照を担当するサービス。

感想と知識不足の情報を chat_logs に紐付けて保存し、
改善材料として使える一覧取得を提供する。
"""

from __future__ import annotations

import sys
from datetime import timedelta, timezone

from chu_ai.services.chat_log_service import (
    has_chat_log_database_config,
    initialize_chat_log_db,
)


VALID_FEEDBACK_TYPES = {
    "helpful",
    "knowledge_missing",
    "wrong_answer",
    "hard_to_understand",
    "other",
}
"""許可するフィードバック種別。"""


def _connect_feedback_postgres():
    """feedback_logs 保存先のPostgreSQL接続を返す。"""
    from chu_ai.services.chat_log_service import _connect_chat_log_postgres

    return _connect_chat_log_postgres()


def initialize_feedback_db() -> None:
    """feedback_logs テーブルとインデックスを作成する。"""
    if not has_chat_log_database_config():
        return

    initialize_chat_log_db()
    with _connect_feedback_postgres() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback_logs (
                    id BIGSERIAL PRIMARY KEY,
                    chat_log_id BIGINT NOT NULL UNIQUE
                        REFERENCES chat_logs(id) ON DELETE CASCADE,
                    helpful BOOLEAN NOT NULL,
                    feedback_type TEXT NOT NULL,
                    comment TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_feedback_logs_created_at
                ON feedback_logs (created_at DESC, id DESC)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_feedback_logs_helpful
                ON feedback_logs (helpful)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_feedback_logs_type
                ON feedback_logs (feedback_type)
                """
            )


def normalize_feedback_type(helpful: bool, feedback_type: str) -> str:
    """感想の真偽に合わせて種別を正規化する。"""
    normalized = (feedback_type or "").strip().lower()
    if helpful:
        return "helpful"
    if normalized in VALID_FEEDBACK_TYPES - {"helpful"}:
        return normalized
    return "other"


def save_feedback(
    *,
    chat_log_id: int,
    helpful: bool,
    feedback_type: str,
    comment: str | None,
) -> int:
    """chat_log_id に紐づくフィードバックを保存または更新する。"""
    if not has_chat_log_database_config():
        raise RuntimeError("CHAT_LOG_POSTGRES_DSN が未設定のため feedback を保存できません。")

    normalized_type = normalize_feedback_type(helpful, feedback_type)
    normalized_comment = (comment or "").strip() or None
    initialize_feedback_db()

    try:
        with _connect_feedback_postgres() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM chat_logs WHERE id = %s", (chat_log_id,))
                if cursor.fetchone() is None:
                    raise ValueError(f"chat_log_id={chat_log_id} が存在しません。")

                cursor.execute(
                    """
                    INSERT INTO feedback_logs (
                        chat_log_id,
                        helpful,
                        feedback_type,
                        comment
                    )
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (chat_log_id)
                    DO UPDATE SET
                        helpful = EXCLUDED.helpful,
                        feedback_type = EXCLUDED.feedback_type,
                        comment = EXCLUDED.comment,
                        updated_at = NOW()
                    RETURNING id
                    """,
                    (
                        chat_log_id,
                        helpful,
                        normalized_type,
                        normalized_comment,
                    ),
                )
                row = cursor.fetchone()
                if not row:
                    raise RuntimeError("feedback の保存結果を取得できませんでした。")
                return int(row[0])
    except Exception as error:
        print(f"Feedback save failed: {error}", file=sys.stderr)
        raise


def list_feedback_logs(
    *,
    limit: int,
    offset: int = 0,
    helpful: bool | None = None,
    feedback_type: str | None = None,
    query_text: str | None = None,
) -> tuple[int, list[dict]]:
    """フィードバックを新しい順に chat_logs の要約付きで返す。"""
    if not has_chat_log_database_config():
        raise RuntimeError("CHAT_LOG_POSTGRES_DSN が未設定のため feedback_logs を参照できません。")

    initialize_feedback_db()

    try:
        from psycopg.rows import dict_row
    except ImportError as error:
        raise RuntimeError(
            "psycopg が見つかりません。`pip install -r src/backend/requirements.txt` を実行してください。"
        ) from error

    conditions: list[str] = []
    params: list[object] = []

    if helpful is not None:
        conditions.append("f.helpful = %s")
        params.append(helpful)

    normalized_type = (feedback_type or "").strip().lower()
    if normalized_type:
        conditions.append("f.feedback_type = %s")
        params.append(normalized_type)

    normalized_query = (query_text or "").strip()
    if normalized_query:
        like = f"%{normalized_query}%"
        conditions.append(
            "(c.question ILIKE %s OR c.answer ILIKE %s OR COALESCE(f.comment, '') ILIKE %s)"
        )
        params.extend([like, like, like])

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    with _connect_feedback_postgres() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                f"""
                SELECT COUNT(*) AS total
                FROM feedback_logs f
                JOIN chat_logs c ON c.id = f.chat_log_id
                {where_clause}
                """,
                params,
            )
            total = int(cursor.fetchone()["total"])

            cursor.execute(
                f"""
                SELECT
                    f.id,
                    f.chat_log_id,
                    f.helpful,
                    f.feedback_type,
                    f.comment,
                    f.created_at,
                    c.asked_at,
                    c.question,
                    c.answer,
                    c.knowledge_mode,
                    c.used_knowledge_files_json
                FROM feedback_logs f
                JOIN chat_logs c ON c.id = f.chat_log_id
                {where_clause}
                ORDER BY f.created_at DESC, f.id DESC
                LIMIT %s
                OFFSET %s
                """,
                [*params, limit, offset],
            )
            rows = cursor.fetchall()

    jst = timezone(timedelta(hours=9))
    items = []
    for row in rows:
        items.append(
            {
                "id": int(row["id"]),
                "chat_log_id": int(row["chat_log_id"]),
                "helpful": bool(row["helpful"]),
                "feedback_type": row["feedback_type"],
                "comment": row["comment"],
                "created_at": row["created_at"].astimezone(jst).isoformat(timespec="seconds"),
                "asked_at": row["asked_at"].astimezone(jst).isoformat(timespec="seconds"),
                "question": row["question"],
                "answer": row["answer"],
                "knowledge_mode": row["knowledge_mode"],
                "used_files": list(row["used_knowledge_files_json"] or []),
            }
        )

    return total, items
