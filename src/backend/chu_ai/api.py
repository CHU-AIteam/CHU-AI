"""FastAPIアプリのHTTP層。

ルーティング、入出力の検証、レスポンス整形を担当し、
知識検索や文章生成の意思決定はservice層へ委譲する。
"""

import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from chu_ai.config import (
    CHAT_HISTORY_MAX_EXCHANGES,
    CHAT_HISTORY_WINDOW_MINUTES,
    CHAT_LOG_ENABLED,
    FRONTEND_DIR,
    GEMINI_MODEL,
    HOME_RETURN_SECONDS,
    HYBRID_AUTO_INIT_DB,
)
from chu_ai.schemas import ChatRequest, ChatResponse
from chu_ai.services.chat_log_service import (
    compact_log_text,
    current_asked_at,
    extract_conversation_history,
    extract_current_question_for_log,
    format_used_files_for_log,
    initialize_chat_log_db,
    resolve_chat_log_db_path,
    save_chat_log,
)
from chu_ai.services.chat_service import FORCED_KNOWLEDGE_MODE, process_user_request
from chu_ai.services.generation_service import normalize_recommended_questions
from chu_ai.services.hybrid_search_service import initialize_hybrid_search_runtime
from chu_ai.services.hybrid_store_service import has_hybrid_database_config


FORCED_SEARCH_BACKEND = "hybrid"
"""クライアント指定や環境変数に関係なく使う検索方式。"""


def _configure_stdio_utf8() -> None:
    """標準入出力の文字化けを防ぐためUTF-8を明示する。"""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")


_configure_stdio_utf8()

app = FastAPI(title="Chu-AI Backend")
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
    if CHAT_LOG_ENABLED:
        initialize_chat_log_db()

    hybrid_init_error = None
    try:
        initialize_hybrid_search_runtime()
    except Exception as error:
        hybrid_init_error = str(error)

    print("Chu-AI runtime settings:")
    print(f"  Knowledge mode: {FORCED_KNOWLEDGE_MODE} (forced)")
    print(f"  Search backend: {FORCED_SEARCH_BACKEND} (forced)")
    print(f"  Home return:    {HOME_RETURN_SECONDS}s")
    print(
        "  Chat history:   "
        f"{CHAT_HISTORY_MAX_EXCHANGES} exchanges / {CHAT_HISTORY_WINDOW_MINUTES:g} min"
    )
    if CHAT_LOG_ENABLED:
        print(f"  Chat log:       enabled ({resolve_chat_log_db_path()})")
    else:
        print("  Chat log:       disabled")
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
        "knowledge_mode_default": FORCED_KNOWLEDGE_MODE,
        "home_return_seconds": HOME_RETURN_SECONDS,
        "chat_history_max_exchanges": CHAT_HISTORY_MAX_EXCHANGES,
        "chat_history_window_minutes": CHAT_HISTORY_WINDOW_MINUTES,
        "chat_log_enabled": CHAT_LOG_ENABLED,
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

    save_chat_log(
        asked_at=asked_at,
        question=current_question,
        answer=generation["answer"],
        can_answer=generation["can_answer"],
        used_knowledge_files=used_files,
        used_conversation=used_conversation,
        recommended_questions=generation["recommended_questions"],
        knowledge_mode=normalized_mode,
        request_text=request_text,
        error_type=generation["error_type"],
        error_message=generation["error_message"],
    )

    print("Chat response:")
    print(f"  Mode:           {normalized_mode}")
    print(f"  Can answer:     {generation['can_answer']}")
    print(f"  Used knowledge: {format_used_files_for_log(used_files)}")
    print(f"  Recommended:    {len(generation['recommended_questions'])} questions")
    print(f"  Answer preview: {compact_log_text(generation['answer'])}")

    return ChatResponse(
        answer=generation["answer"],
        can_answer=generation["can_answer"],
        recommended_questions=generation["recommended_questions"],
        used_files=used_files,
        knowledge_mode=normalized_mode,
    )


@app.get("/")
def frontend_root() -> FileResponse:
    """チャットUIのトップページ"""
    return FileResponse(FRONTEND_DIR / "index.html")


# `/api/*` は上で定義済み。ここでは静的ファイルのみ配信する。
app.mount("/", StaticFiles(directory=FRONTEND_DIR), name="frontend-static")
