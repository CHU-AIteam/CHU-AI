"""設置PCに出す診断と、セットアップ時の疎通確認。"""
from __future__ import annotations

import json
import os
import re

from chu_ai.config import PROJECT_ROOT


ISSUES = {
    "gemini_key": "Geminiの接続情報を確認してください。「初回設定・接続を直す」で有効なキーを保存してから再試行できます。",
    "gemini_limit": "Geminiの利用上限に達しています。時間をおいて再試行するか、担当者に契約・利用枠を確認してください。",
    "gemini_model": "設定されているGeminiモデルを利用できません。担当者にモデルの設定を確認してください。",
    "gemini_service": "Geminiから正常な応答がありません。少し待ってから再試行してください。",
    "network": "Geminiに接続できません。PCのインターネット接続を確認してから再試行してください。",
    "database": "知識の検索データベースを利用できません。コモ管理の「再起動」を試し、続く場合は担当者へ記録を渡してください。",
    "storage": "知識の保存に失敗しました。設置PCの空き容量と保存フォルダを担当者に確認してください。",
    "interrupted": "前回の処理が途中で止まりました。反映状況を確認し、必要なら再試行してください。",
    "unknown": "処理を完了できませんでした。もう一度試し、続く場合は担当者へ診断記録を渡してください。",
}


class OperationIssue(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(ISSUES[code])


def diagnose(error: Exception, stage: str) -> OperationIssue:
    """外部応答は分類にだけ使い、画面・診断ファイルには定型文を返す。"""
    cause: BaseException | None = error
    seen: set[int] = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, OperationIssue):
            return cause
        module = type(cause).__module__
        if module.startswith("psycopg"):
            return OperationIssue("database")
        if stage in {"embedding", "chat"}:
            status = getattr(cause, "code", None)
            text = str(cause).upper()
            if status in (401, 403) or "API_KEY" in text or "PERMISSION_DENIED" in text:
                return OperationIssue("gemini_key")
            if status in (402, 429) or "RESOURCE_EXHAUSTED" in text or re.search(r"\b429\b", text):
                return OperationIssue("gemini_limit")
            if status == 404 or "NOT_FOUND" in text:
                return OperationIssue("gemini_model")
            if isinstance(cause, (TimeoutError, ConnectionError)) or module.startswith(("httpx", "httpcore", "requests", "socket")):
                return OperationIssue("network")
        cause = cause.__cause__ or cause.__context__
    if stage in {"database", "publishing"}:
        return OperationIssue("database")
    if stage in {"preparing", "prepared"} and isinstance(error, OSError):
        return OperationIssue("storage")
    return OperationIssue("gemini_service" if stage in {"embedding", "chat"} else "unknown")


def check_connection() -> dict:
    from chu_ai.services.embedding_service import embed_query_text

    try:
        embed_query_text("中部大学コモンズ")
    except Exception as error:
        raise diagnose(error, "embedding") from error
    return {"ok": True}


def check_chat() -> dict:
    """普段の回答処理を通し、試し質問を来場者の会話ログに加えない。"""
    from chu_ai.services.chat_service import process_user_request

    question = "中部大学のコモンズはどんな場所ですか？"
    try:
        generation, _, _ = process_user_request(question, "search")
    except Exception as error:
        raise diagnose(error, "chat") from error
    if generation["error_type"] or not generation["can_answer"] or not generation["answer"].strip():
        error = RuntimeError(generation.get("error_message") or "")
        raise diagnose(error, "chat")
    return {"ok": True}


def windows_kiosk_state() -> dict:
    revision = os.getenv("COMO_WINDOWS_REVISION", "")
    if not re.fullmatch(r"[0-9a-f]{12}|legacy", revision):
        return {"windows_revision": "", "kiosk_chat_paused": False}
    try:
        state = json.loads((PROJECT_ROOT / "data" / "windows-launcher" / "kiosk.json").read_text(encoding="utf-8"))
        paused = state.get("chat_paused") is True
    except (OSError, ValueError, AttributeError):
        paused = False
    return {"windows_revision": revision, "kiosk_chat_paused": paused}
