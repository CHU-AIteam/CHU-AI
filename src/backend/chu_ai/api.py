"""FastAPIアプリのHTTP層。

ルーティング、入出力の検証、レスポンス整形を担当し、
知識検索や文章生成の意思決定はservice層へ委譲する。
"""

import sys

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from chu_ai.config import (
    CHAT_LOG_ADMIN_API_KEY,
    CHAT_HISTORY_MAX_EXCHANGES,
    CHAT_HISTORY_WINDOW_MINUTES,
    CHAT_LOG_ENABLED,
    CHAT_LOG_LIST_DEFAULT_LIMIT,
    CHAT_LOG_LIST_MAX_LIMIT,
    FRONTEND_DIR,
    GEMINI_MODEL,
    HOME_RETURN_SECONDS,
    HYBRID_AUTO_INIT_DB,
    ROUTER_CONFIDENCE_THRESHOLD,
    ROUTER_ENABLED,
    ROUTER_MODEL,
)
from chu_ai.schemas import (
    ChatLogListResponse,
    ChatRequest,
    ChatResponse,
    FeedbackLogListResponse,
    FeedbackRequest,
    FeedbackResponse,
)
from chu_ai.services.chat_log_service import (
    compact_log_text,
    current_asked_at,
    extract_conversation_history,
    extract_current_question_for_log,
    format_used_files_for_log,
)
from chu_ai.services.chat_service import FORCED_KNOWLEDGE_MODE, process_user_request
from chu_ai.services.generation_service import normalize_recommended_questions
from chu_ai.services.hybrid_search_service import initialize_hybrid_search_runtime
from chu_ai.services.hybrid_store_service import has_hybrid_database_config
from chu_ai.services.log_storage_service import (
    get_log_storage_label,
    has_log_storage_config,
    initialize_log_storage,
    list_chat_log_entries,
    list_feedback_log_entries,
    save_chat_log_entry,
    save_feedback_entry,
)


FORCED_SEARCH_BACKEND = "hybrid"
"""クライアント指定や環境変数に関係なく使う検索方式。"""


def _require_admin_api_key(x_admin_key: str | None = Header(default=None)) -> None:
    """管理API用の固定キーを検証する。"""
    if not CHAT_LOG_ADMIN_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="CHAT_LOG_ADMIN_API_KEY が未設定のため管理APIは無効です。",
        )
    if x_admin_key != CHAT_LOG_ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="管理APIキーが不正です。")


def _configure_stdio_utf8() -> None:
    """標準入出力の文字化けを防ぐためUTF-8を明示する。"""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")


_configure_stdio_utf8()

app = FastAPI(title="Chubu Commons AI Backend")
"""フロントエンドから呼び出されるAPI"""

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup_log() -> None:
    """バックエンド起動時の設定表示"""
    chat_log_init_error = None
    if CHAT_LOG_ENABLED:
        try:
            initialize_log_storage()
        except Exception as error:
            chat_log_init_error = str(error)

    hybrid_init_error = None
    try:
        initialize_hybrid_search_runtime()
    except Exception as error:
        hybrid_init_error = str(error)

    print("Chubu Commons AI runtime settings:")
    print(f"  Knowledge mode: {FORCED_KNOWLEDGE_MODE} (forced)")
    print(f"  Search backend: {FORCED_SEARCH_BACKEND} (forced)")
    print(f"  Home return:    {HOME_RETURN_SECONDS}s")
    print(
        "  Chat history:   "
        f"{CHAT_HISTORY_MAX_EXCHANGES} exchanges / {CHAT_HISTORY_WINDOW_MINUTES:g} min"
    )
    if CHAT_LOG_ENABLED:
        chat_log_status = get_log_storage_label()
        if chat_log_init_error:
            chat_log_status = f"{chat_log_status}, init failed ({chat_log_init_error})"
        elif not has_log_storage_config():
            chat_log_status = f"{chat_log_status}, not configured"
        print(f"  Chat log:       enabled ({chat_log_status})")
    else:
        print("  Chat log:       disabled")
    print(
        "  Admin log API:  "
        f"{'enabled' if CHAT_LOG_ADMIN_API_KEY else 'disabled'}"
    )
    print(f"  Hybrid DB:      {'configured' if has_hybrid_database_config() else 'not configured'}")
    print(f"  Hybrid autoinit:{'enabled' if HYBRID_AUTO_INIT_DB else 'disabled'}")
    if hybrid_init_error:
        print(f"  Hybrid init:    failed ({hybrid_init_error})")
    else:
        print("  Hybrid init:    ok")


@app.get("/api/health")
def health() -> dict:
    """起動確認用API"""
    return {
        "status": "ok",
        "model": GEMINI_MODEL,
        "router_enabled": ROUTER_ENABLED,
        "router_model": ROUTER_MODEL,
        "router_confidence_threshold": ROUTER_CONFIDENCE_THRESHOLD,
        "knowledge_mode_default": FORCED_KNOWLEDGE_MODE,
        "home_return_seconds": HOME_RETURN_SECONDS,
        "chat_history_max_exchanges": CHAT_HISTORY_MAX_EXCHANGES,
        "chat_history_window_minutes": CHAT_HISTORY_WINDOW_MINUTES,
        "chat_log_enabled": CHAT_LOG_ENABLED,
        "chat_log_storage": get_log_storage_label(),
        "chat_log_db_configured": has_log_storage_config(),
        "chat_log_admin_api_enabled": bool(CHAT_LOG_ADMIN_API_KEY),
        "search_backend_default": FORCED_SEARCH_BACKEND,
        "hybrid_db_configured": has_hybrid_database_config(),
    }


@app.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    """フロントエンドから質問を受け取り、回答を返すAPI"""
    request_text = request.text.strip()
    if not request_text:
        return ChatResponse(
            answer="質問を入力してね。",
            can_answer=False,
            response_type="unknown",
            recommended_questions=normalize_recommended_questions([], ""),
            used_files=[],
            knowledge_mode=FORCED_KNOWLEDGE_MODE,
        )

    asked_at = current_asked_at()
    current_question = extract_current_question_for_log(request_text)
    used_conversation = extract_conversation_history(request_text)

    generation, used_files, normalized_mode = process_user_request(
        request_text, request.knowledge_mode
    )

    chat_log_id = save_chat_log_entry(
        asked_at=asked_at,
        question=current_question,
        answer=generation["answer"],
        can_answer=generation["can_answer"],
        used_knowledge_files=used_files,
        used_conversation=used_conversation,
        recommended_questions=generation["recommended_questions"],
        knowledge_mode=normalized_mode,
        request_text=request_text,
        router_route=generation["router_route"],
        router_response_type=generation["router_response_type"],
        router_confidence=generation["router_confidence"],
        router_reason=generation["router_reason"],
        router_skipped_rag=generation["router_skipped_rag"],
        error_type=generation["error_type"],
        error_message=generation["error_message"],
    )

    print("Chat response:")
    print(f"  Mode:           {normalized_mode}")
    print(f"  Can answer:     {generation['can_answer']}")
    print(f"  Response type:  {generation['response_type']}")
    print(f"  Used knowledge: {format_used_files_for_log(used_files)}")
    print(f"  Recommended:    {len(generation['recommended_questions'])} questions")
    print(f"  Answer preview: {compact_log_text(generation['answer'])}")

    return ChatResponse(
        answer=generation["answer"],
        can_answer=generation["can_answer"],
        response_type=generation["response_type"],
        recommended_questions=generation["recommended_questions"],
        used_files=used_files,
        knowledge_mode=normalized_mode,
        chat_log_id=chat_log_id,
    )


@app.post("/api/feedback", response_model=FeedbackResponse)
def feedback(request: FeedbackRequest) -> FeedbackResponse:
    """回答1件に対する感想・知識不足フィードバックを保存するAPI"""
    try:
        feedback_id = save_feedback_entry(
            chat_log_id=request.chat_log_id,
            helpful=request.helpful,
            feedback_type=request.feedback_type,
            comment=request.comment,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"feedback save failed: {error}") from error

    return FeedbackResponse(feedback_id=feedback_id)


@app.get("/api/admin/chat-logs", response_model=ChatLogListResponse)
def admin_chat_logs(
    limit: int = Query(
        default=CHAT_LOG_LIST_DEFAULT_LIMIT,
        ge=1,
        le=CHAT_LOG_LIST_MAX_LIMIT,
    ),
    offset: int = Query(default=0, ge=0),
    can_answer: bool | None = None,
    knowledge_mode: str | None = None,
    q: str | None = Query(default=None, max_length=200),
    x_admin_key: str | None = Header(default=None),
) -> ChatLogListResponse:
    """認証付きでチャットログ一覧を返すAPI"""
    _require_admin_api_key(x_admin_key)
    try:
        total, items = list_chat_log_entries(
            limit=limit,
            offset=offset,
            can_answer=can_answer,
            knowledge_mode=knowledge_mode,
            query_text=q,
        )
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=503, detail=f"chat log list failed: {error}") from error
    return ChatLogListResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=items,
    )


@app.get("/api/admin/feedback-logs", response_model=FeedbackLogListResponse)
def admin_feedback_logs(
    limit: int = Query(
        default=CHAT_LOG_LIST_DEFAULT_LIMIT,
        ge=1,
        le=CHAT_LOG_LIST_MAX_LIMIT,
    ),
    offset: int = Query(default=0, ge=0),
    helpful: bool | None = None,
    feedback_type: str | None = None,
    q: str | None = Query(default=None, max_length=200),
    x_admin_key: str | None = Header(default=None),
) -> FeedbackLogListResponse:
    """認証付きでフィードバック一覧を返すAPI"""
    _require_admin_api_key(x_admin_key)
    try:
        total, items = list_feedback_log_entries(
            limit=limit,
            offset=offset,
            helpful=helpful,
            feedback_type=feedback_type,
            query_text=q,
        )
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=503, detail=f"feedback log list failed: {error}") from error

    return FeedbackLogListResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=items,
    )


@app.get("/")
def frontend_root() -> FileResponse:
    """チャットUIのトップページ"""
    return FileResponse(FRONTEND_DIR / "index.html")


# `/api/*` は上で定義済み。ここでは静的ファイルのみ配信する。
app.mount("/", StaticFiles(directory=FRONTEND_DIR), name="frontend-static")
