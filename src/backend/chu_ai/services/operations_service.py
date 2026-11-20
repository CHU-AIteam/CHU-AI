"""設置PCに出す診断と、セットアップ時の疎通確認。"""
from __future__ import annotations

import json
import os
import re

from chu_ai.config import PROJECT_ROOT


ISSUES = {
    "gemini_key": "コモをつなぐためのコードを使えませんでした。デスクトップの「コモ管理」で「最初の設定を見直す」を選んでください。",
    "gemini_limit": "質問に答えるために使える量が上限に達しました。時間をおいて、もう一度試してください。",
    "gemini_model": "今の設定では、コモが質問に答えられません。設置を担当した人に記録を渡してください。",
    "gemini_service": "質問に答えるための通信を確かめられませんでした。少し待って、もう一度試してください。",
    "network": "質問に答えるための通信ができません。パソコンがインターネットにつながっているか確かめてください。",
    "database": "案内を読み出せませんでした。「コモ管理」の「コモを開き直す」を試してください。",
    "storage": "案内をこのパソコンに保存できませんでした。設置を担当した人に記録を渡してください。",
    "interrupted": "前回は途中で止まりました。案内が変わったか確かめてから、もう一度試してください。",
    "unknown": "最後まで終えられませんでした。もう一度試し、続くときは担当者に記録を渡してください。",
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
