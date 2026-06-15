"""Google Sheets へのログ保存と参照を担当するサービス。

学校Wi-Fiで外部PostgreSQLポートが塞がれる場合でも使えるように、
HTTPS経由のGoogle Sheets APIで chat_logs / feedback_logs を扱う。
"""

from __future__ import annotations

import json
import ssl
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from chu_ai.config import (
    GOOGLE_CHAT_LOG_SHEET_NAME,
    GOOGLE_FEEDBACK_LOG_SHEET_NAME,
    GOOGLE_SERVICE_ACCOUNT_FILE,
    GOOGLE_SHEETS_SPREADSHEET_ID,
)


SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

CHAT_LOG_HEADERS = [
    "chat_log_id",
    "asked_at",
    "question",
    "answer",
    "can_answer",
    "used_files_json",
    "used_conversation_json",
    "recommended_questions_json",
    "knowledge_mode",
    "request_text",
    "error_type",
    "error_message",
    "created_at",
    "router_route",
    "router_response_type",
    "router_confidence",
    "router_reason",
    "router_skipped_rag",
]

FEEDBACK_LOG_HEADERS = [
    "feedback_id",
    "chat_log_id",
    "helpful",
    "feedback_type",
    "comment",
    "created_at",
]

_initialized_sheets: set[str] = set()

SHEETS_API_MAX_ATTEMPTS = 3
"""Google APIへの一時的な通信失敗を吸収する試行回数。"""


def has_google_sheets_config() -> bool:
    """Google Sheets保存に必要な設定があるか返す。"""
    return bool(GOOGLE_SERVICE_ACCOUNT_FILE and GOOGLE_SHEETS_SPREADSHEET_ID)


def _now_jst() -> str:
    """ログIDと保存日時に使う日本時間を返す。"""
    jst = timezone(timedelta(hours=9))
    return datetime.now(jst).isoformat(timespec="seconds")


def _make_log_id(prefix: str) -> str:
    """Google Sheets用の衝突しにくい文字列IDを作る。"""
    stamp = datetime.now(timezone(timedelta(hours=9))).strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{stamp}_{uuid.uuid4().hex[:8]}"


def _json_text(value: object) -> str:
    """Sheetsに保存しやすいJSON文字列へ変換する。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _parse_json_list(value: str | None) -> list:
    """Sheets上のJSON文字列をlistへ戻す。"""
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _bool_text(value: bool) -> str:
    """Sheetsで扱いやすい真偽値文字列へ変換する。"""
    return "true" if value else "false"


def _parse_bool(value: str | bool | None) -> bool:
    """Sheetsから読んだ真偽値をboolへ戻す。"""
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_optional_bool(value: str | bool | None) -> bool | None:
    """空欄をNoneとして扱い、値がある時だけboolへ戻す。"""
    if value is None or str(value).strip() == "":
        return None
    return _parse_bool(value)


def _parse_float_or_none(value: str | None) -> float | None:
    """Sheetsから読んだ数値文字列をfloatへ戻す。"""
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _quote_sheet_name(sheet_name: str) -> str:
    """Google Sheets APIのrange用にシート名をクォートする。"""
    escaped = sheet_name.replace("'", "''")
    return f"'{escaped}'"


def _execute_sheets_request(
    method: str,
    url: str,
    *,
    params: dict[str, str] | None = None,
    json_body: dict | None = None,
) -> dict:
    """Google Sheets APIリクエストをリトライ付きで実行する。"""
    session = _get_sheets_session()
    last_error: Exception | None = None
    for attempt in range(1, SHEETS_API_MAX_ATTEMPTS + 1):
        try:
            response = session.request(
                method,
                url,
                params=params,
                json=json_body,
                timeout=10,
            )
            if response.status_code < 400:
                return response.json() if response.content else {}

            if response.status_code not in {429, 500, 502, 503, 504}:
                raise RuntimeError(
                    f"Google Sheets API error {response.status_code}: {response.text}"
                )
            last_error = RuntimeError(
                f"Google Sheets API error {response.status_code}: {response.text}"
            )
        except Exception as error:
            if not isinstance(error, (ssl.SSLError, TimeoutError, OSError)):
                raise
            last_error = error

        if attempt < SHEETS_API_MAX_ATTEMPTS:
            time.sleep(0.5 * attempt)

    if last_error is not None:
        raise last_error
    raise RuntimeError("Google Sheets API request failed.")


@lru_cache(maxsize=1)
def _get_sheets_session():
    """Google認証済みHTTPセッションを返す。"""
    if not has_google_sheets_config():
        raise RuntimeError("Google Sheets保存に必要な設定が未設定です。")

    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession
    except ImportError as error:
        raise RuntimeError(
            "Google Sheets API認証依存関係が見つかりません。requirements.txt を反映してください。"
        ) from error

    credential_path = Path(GOOGLE_SERVICE_ACCOUNT_FILE)
    if not credential_path.exists():
        raise RuntimeError(f"GoogleサービスアカウントJSONが見つかりません: {credential_path}")

    credentials = service_account.Credentials.from_service_account_file(
        str(credential_path),
        scopes=SHEETS_SCOPES,
    )
    return AuthorizedSession(credentials)


def _sheets_url(path: str) -> str:
    """Google Sheets APIのURLを組み立てる。"""
    base = f"https://sheets.googleapis.com/v4/spreadsheets/{GOOGLE_SHEETS_SPREADSHEET_ID}"
    return f"{base}{path}"


def _values_url(range_name: str, suffix: str = "") -> str:
    """values API用のURLを組み立てる。"""
    encoded_range = quote(range_name, safe="")
    return _sheets_url(f"/values/{encoded_range}{suffix}")


def _sheet_titles() -> set[str]:
    """スプレッドシート内のシート名一覧を返す。"""
    response = _execute_sheets_request(
        "GET",
        _sheets_url(""),
        params={"fields": "sheets.properties.title"},
    )
    return {
        sheet["properties"]["title"]
        for sheet in response.get("sheets", [])
        if sheet.get("properties", {}).get("title")
    }


def _ensure_sheet(sheet_name: str, headers: list[str]) -> None:
    """指定シートを作成し、ヘッダー行を整える。"""
    if sheet_name in _initialized_sheets:
        return

    if sheet_name not in _sheet_titles():
        _execute_sheets_request(
            "POST",
            _sheets_url(":batchUpdate"),
            json_body={"requests": [{"addSheet": {"properties": {"title": sheet_name}}}]},
        )

    quoted_name = _quote_sheet_name(sheet_name)
    header_response = _execute_sheets_request(
        "GET",
        _values_url(f"{quoted_name}!1:1"),
    )
    first_row = header_response.get("values", [[]])[0]
    if first_row != headers:
        _execute_sheets_request(
            "PUT",
            _values_url(f"{quoted_name}!A1"),
            params={"valueInputOption": "RAW"},
            json_body={"values": [headers]},
        )

    _initialized_sheets.add(sheet_name)


def initialize_google_sheets_log_storage() -> None:
    """Google Sheets保存先を初期化する。"""
    if not has_google_sheets_config():
        return
    _ensure_sheet(GOOGLE_CHAT_LOG_SHEET_NAME, CHAT_LOG_HEADERS)
    _ensure_sheet(GOOGLE_FEEDBACK_LOG_SHEET_NAME, FEEDBACK_LOG_HEADERS)


def save_chat_log_to_google_sheets(
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
) -> str:
    """チャットログをGoogle Sheetsへ1行追記してIDを返す。"""
    initialize_google_sheets_log_storage()
    chat_log_id = _make_log_id("chat")
    row = [
        chat_log_id,
        asked_at,
        question,
        answer,
        _bool_text(can_answer),
        _json_text(used_knowledge_files),
        _json_text(used_conversation),
        _json_text(recommended_questions),
        knowledge_mode,
        request_text,
        error_type or "",
        error_message or "",
        _now_jst(),
        router_route or "",
        router_response_type or "",
        "" if router_confidence is None else str(router_confidence),
        router_reason or "",
        "" if router_skipped_rag is None else _bool_text(router_skipped_rag),
    ]

    _append_row(GOOGLE_CHAT_LOG_SHEET_NAME, row)
    return chat_log_id


def save_feedback_to_google_sheets(
    *,
    chat_log_id: str,
    helpful: bool,
    feedback_type: str,
    comment: str | None,
) -> str:
    """フィードバックをGoogle Sheetsへ1行追記してIDを返す。"""
    if not find_google_sheets_chat_log(chat_log_id):
        raise ValueError(f"chat_log_id={chat_log_id} が存在しません。")

    initialize_google_sheets_log_storage()
    feedback_id = _make_log_id("feedback")
    row = [
        feedback_id,
        chat_log_id,
        _bool_text(helpful),
        feedback_type,
        (comment or "").strip(),
        _now_jst(),
    ]

    _append_row(GOOGLE_FEEDBACK_LOG_SHEET_NAME, row)
    return feedback_id


def _append_row(sheet_name: str, row: list[object]) -> None:
    """指定シートへ1行追加する。"""
    quoted_name = _quote_sheet_name(sheet_name)
    _execute_sheets_request(
        "POST",
        _values_url(f"{quoted_name}!A1", ":append"),
        params={
            "valueInputOption": "RAW",
            "insertDataOption": "INSERT_ROWS",
        },
        json_body={"values": [row]},
    )


def _read_sheet_records(sheet_name: str, headers: list[str]) -> list[dict[str, str]]:
    """シート全体をdictのリストとして読む。"""
    _ensure_sheet(sheet_name, headers)
    quoted_name = _quote_sheet_name(sheet_name)
    response = _execute_sheets_request(
        "GET",
        _values_url(f"{quoted_name}!A:Z"),
    )
    rows = response.get("values", [])
    if len(rows) <= 1:
        return []

    records = []
    for row in rows[1:]:
        padded = [*row, *[""] * max(len(headers) - len(row), 0)]
        records.append(dict(zip(headers, padded[: len(headers)])))
    return records


def find_google_sheets_chat_log(chat_log_id: str) -> dict[str, str] | None:
    """chat_log_idに一致するチャットログを返す。"""
    normalized_id = str(chat_log_id).strip()
    for record in _read_sheet_records(GOOGLE_CHAT_LOG_SHEET_NAME, CHAT_LOG_HEADERS):
        if record.get("chat_log_id") == normalized_id:
            return record
    return None


def list_google_sheets_chat_logs(
    *,
    limit: int,
    offset: int = 0,
    can_answer: bool | None = None,
    knowledge_mode: str | None = None,
    query_text: str | None = None,
) -> tuple[int, list[dict]]:
    """Google Sheetsからチャットログ一覧を返す。"""
    records = _read_sheet_records(GOOGLE_CHAT_LOG_SHEET_NAME, CHAT_LOG_HEADERS)
    normalized_mode = (knowledge_mode or "").strip().lower()
    normalized_query = (query_text or "").strip().lower()

    def matches(record: dict[str, str]) -> bool:
        if can_answer is not None and _parse_bool(record.get("can_answer")) != can_answer:
            return False
        if normalized_mode and record.get("knowledge_mode", "").lower() != normalized_mode:
            return False
        if normalized_query:
            haystack = f"{record.get('question', '')}\n{record.get('answer', '')}".lower()
            return normalized_query in haystack
        return True

    filtered = [record for record in records if matches(record)]
    filtered.reverse()
    items = [_chat_record_to_item(record) for record in filtered[offset : offset + limit]]
    return len(filtered), items


def list_google_sheets_feedback_logs(
    *,
    limit: int,
    offset: int = 0,
    helpful: bool | None = None,
    feedback_type: str | None = None,
    query_text: str | None = None,
) -> tuple[int, list[dict]]:
    """Google Sheetsからフィードバック一覧を返す。"""
    feedback_records = _read_sheet_records(GOOGLE_FEEDBACK_LOG_SHEET_NAME, FEEDBACK_LOG_HEADERS)
    chat_records = _read_sheet_records(GOOGLE_CHAT_LOG_SHEET_NAME, CHAT_LOG_HEADERS)
    chat_by_id = {record.get("chat_log_id", ""): record for record in chat_records}
    normalized_type = (feedback_type or "").strip().lower()
    normalized_query = (query_text or "").strip().lower()

    def matches(record: dict[str, str]) -> bool:
        chat = chat_by_id.get(record.get("chat_log_id", ""), {})
        if helpful is not None and _parse_bool(record.get("helpful")) != helpful:
            return False
        if normalized_type and record.get("feedback_type", "").lower() != normalized_type:
            return False
        if normalized_query:
            haystack = "\n".join(
                [
                    record.get("comment", ""),
                    chat.get("question", ""),
                    chat.get("answer", ""),
                ]
            ).lower()
            return normalized_query in haystack
        return True

    filtered = [record for record in feedback_records if matches(record)]
    filtered.reverse()
    items = [
        _feedback_record_to_item(record, chat_by_id.get(record.get("chat_log_id", ""), {}))
        for record in filtered[offset : offset + limit]
    ]
    return len(filtered), items


def _chat_record_to_item(record: dict[str, str]) -> dict:
    """Sheetsのchat_logs行をAPIレスポンス向けに整形する。"""
    return {
        "id": record.get("chat_log_id", ""),
        "asked_at": record.get("asked_at", ""),
        "question": record.get("question", ""),
        "answer": record.get("answer", ""),
        "can_answer": _parse_bool(record.get("can_answer")),
        "used_files": _parse_json_list(record.get("used_files_json")),
        "used_conversation": _parse_json_list(record.get("used_conversation_json")),
        "recommended_questions": _parse_json_list(record.get("recommended_questions_json")),
        "knowledge_mode": record.get("knowledge_mode", ""),
        "request_text": record.get("request_text", ""),
        "router_route": record.get("router_route") or None,
        "router_response_type": record.get("router_response_type") or None,
        "router_confidence": _parse_float_or_none(record.get("router_confidence")),
        "router_reason": record.get("router_reason") or None,
        "router_skipped_rag": _parse_optional_bool(record.get("router_skipped_rag")),
        "error_type": record.get("error_type") or None,
        "error_message": record.get("error_message") or None,
    }


def _feedback_record_to_item(record: dict[str, str], chat: dict[str, str]) -> dict:
    """Sheetsのfeedback_logs行をAPIレスポンス向けに整形する。"""
    return {
        "id": record.get("feedback_id", ""),
        "chat_log_id": record.get("chat_log_id", ""),
        "helpful": _parse_bool(record.get("helpful")),
        "feedback_type": record.get("feedback_type") or "other",
        "comment": record.get("comment") or None,
        "created_at": record.get("created_at", ""),
        "asked_at": chat.get("asked_at", ""),
        "question": chat.get("question", ""),
        "answer": chat.get("answer", ""),
        "knowledge_mode": chat.get("knowledge_mode", ""),
        "used_files": _parse_json_list(chat.get("used_files_json")),
    }


def print_google_sheets_error(message: str, error: Exception) -> None:
    """保存失敗を標準エラーへ短く出す。"""
    print(f"{message}: {error}", file=sys.stderr)
