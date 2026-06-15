"""ログ保存先の切り替えを担当するサービス。

API層から保存先の違いを隠し、PostgreSQLとGoogle Sheetsを
環境変数 LOG_STORAGE_MODE で切り替える。
"""

from __future__ import annotations

import sys

from chu_ai.config import CHAT_LOG_ENABLED, LOG_STORAGE_MODE
from chu_ai.services.chat_log_service import (
    get_chat_log_storage_label as get_postgres_storage_label,
    has_chat_log_database_config,
    initialize_chat_log_db,
    list_chat_logs as list_postgres_chat_logs,
    save_chat_log as save_postgres_chat_log,
)
from chu_ai.services.feedback_service import (
    initialize_feedback_db,
    list_feedback_logs as list_postgres_feedback_logs,
    normalize_feedback_type,
    save_feedback as save_postgres_feedback,
)
from chu_ai.services.google_sheets_log_service import (
    has_google_sheets_config,
    initialize_google_sheets_log_storage,
    list_google_sheets_chat_logs,
    list_google_sheets_feedback_logs,
    print_google_sheets_error,
    save_chat_log_to_google_sheets,
    save_feedback_to_google_sheets,
)


def is_google_sheets_storage() -> bool:
    """ログ保存先がGoogle Sheetsか返す。"""
    return LOG_STORAGE_MODE == "google_sheets"


def get_log_storage_label() -> str:
    """起動ログやヘルスチェック向けの保存先ラベルを返す。"""
    if is_google_sheets_storage():
        return "google_sheets"
    return get_postgres_storage_label()


def has_log_storage_config() -> bool:
    """選択中のログ保存先が設定済みか返す。"""
    if not CHAT_LOG_ENABLED:
        return False
    if is_google_sheets_storage():
        return has_google_sheets_config()
    return has_chat_log_database_config()


def initialize_log_storage() -> None:
    """選択中のログ保存先を初期化する。"""
    if not CHAT_LOG_ENABLED:
        return
    if is_google_sheets_storage():
        initialize_google_sheets_log_storage()
        return
    initialize_chat_log_db()
    initialize_feedback_db()


def save_chat_log_entry(
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
    router_route: str | None = None,
    router_response_type: str | None = None,
    router_confidence: float | None = None,
    router_reason: str | None = None,
    router_skipped_rag: bool | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
) -> str | None:
    """チャットログを選択中の保存先へ保存し、IDを文字列で返す。"""
    if not CHAT_LOG_ENABLED:
        return None

    if is_google_sheets_storage():
        try:
            return save_chat_log_to_google_sheets(
                asked_at=asked_at,
                question=question,
                answer=answer,
                can_answer=can_answer,
                used_knowledge_files=used_knowledge_files,
                used_conversation=used_conversation,
                recommended_questions=recommended_questions,
                knowledge_mode=knowledge_mode,
                request_text=request_text,
                router_route=router_route,
                router_response_type=router_response_type,
                router_confidence=router_confidence,
                router_reason=router_reason,
                router_skipped_rag=router_skipped_rag,
                error_type=error_type,
                error_message=error_message,
            )
        except Exception as error:
            print_google_sheets_error("Google Sheets chat log save failed", error)
            return None

    chat_log_id = save_postgres_chat_log(
        asked_at=asked_at,
        question=question,
        answer=answer,
        can_answer=can_answer,
        used_knowledge_files=used_knowledge_files,
        used_conversation=used_conversation,
        recommended_questions=recommended_questions,
        knowledge_mode=knowledge_mode,
        request_text=request_text,
        router_route=router_route,
        router_response_type=router_response_type,
        router_confidence=router_confidence,
        router_reason=router_reason,
        router_skipped_rag=router_skipped_rag,
        error_type=error_type,
        error_message=error_message,
    )
    return str(chat_log_id) if chat_log_id is not None else None


def save_feedback_entry(
    *,
    chat_log_id: str,
    helpful: bool,
    feedback_type: str,
    comment: str | None,
) -> str:
    """フィードバックを選択中の保存先へ保存し、IDを文字列で返す。"""
    normalized_type = normalize_feedback_type(helpful, feedback_type)
    if is_google_sheets_storage():
        return save_feedback_to_google_sheets(
            chat_log_id=chat_log_id,
            helpful=helpful,
            feedback_type=normalized_type,
            comment=comment,
        )

    try:
        postgres_chat_log_id = int(chat_log_id)
    except ValueError as error:
        raise ValueError(f"chat_log_id={chat_log_id} が不正です。") from error

    feedback_id = save_postgres_feedback(
        chat_log_id=postgres_chat_log_id,
        helpful=helpful,
        feedback_type=normalized_type,
        comment=comment,
    )
    return str(feedback_id)


def list_chat_log_entries(
    *,
    limit: int,
    offset: int = 0,
    can_answer: bool | None = None,
    knowledge_mode: str | None = None,
    query_text: str | None = None,
) -> tuple[int, list[dict]]:
    """選択中の保存先からチャットログ一覧を返す。"""
    if is_google_sheets_storage():
        return list_google_sheets_chat_logs(
            limit=limit,
            offset=offset,
            can_answer=can_answer,
            knowledge_mode=knowledge_mode,
            query_text=query_text,
        )

    total, items = list_postgres_chat_logs(
        limit=limit,
        offset=offset,
        can_answer=can_answer,
        knowledge_mode=knowledge_mode,
        query_text=query_text,
    )
    for item in items:
        item["id"] = str(item["id"])
    return total, items


def list_feedback_log_entries(
    *,
    limit: int,
    offset: int = 0,
    helpful: bool | None = None,
    feedback_type: str | None = None,
    query_text: str | None = None,
) -> tuple[int, list[dict]]:
    """選択中の保存先からフィードバック一覧を返す。"""
    if is_google_sheets_storage():
        return list_google_sheets_feedback_logs(
            limit=limit,
            offset=offset,
            helpful=helpful,
            feedback_type=feedback_type,
            query_text=query_text,
        )

    total, items = list_postgres_feedback_logs(
        limit=limit,
        offset=offset,
        helpful=helpful,
        feedback_type=feedback_type,
        query_text=query_text,
    )
    for item in items:
        item["id"] = str(item["id"])
        item["chat_log_id"] = str(item["chat_log_id"])
    return total, items


def print_log_storage_startup_error(error: Exception) -> None:
    """起動時ログ保存初期化の失敗を標準エラーへ出す。"""
    print(f"Log storage init failed: {error}", file=sys.stderr)
