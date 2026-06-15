"""RAG検索前の事前分類を担当するサービス。

明らかな雑談やアプリの使い方だけを direct にし、
迷う質問は必ず rag に戻すことで事実回答の安全性を保つ。
"""

from dataclasses import dataclass

from chu_ai.config import (
    ROUTER_CONFIDENCE_THRESHOLD,
    ROUTER_ENABLED,
    ROUTER_MODEL,
)
from chu_ai.prompts import ROUTER_JSON_INSTRUCTION, ROUTER_PROMPT
from chu_ai.services.generation_service import call_gemini_api, extract_json_object


VALID_ROUTES = {"direct", "rag"}
"""ルーターが返してよい経路"""

VALID_ROUTER_RESPONSE_TYPES = {
    "chat",
    "knowledge",
    "unknown",
    "clarify",
    "usage",
}
"""ルーターが返してよい回答分類"""

DIRECT_RESPONSE_TYPES = {"chat", "usage"}
"""RAG検索をスキップしてよい回答分類"""


@dataclass(frozen=True)
class RouteDecision:
    """RAG検索前の分類結果"""

    route: str
    response_type: str
    confidence: float
    reason: str
    raw_text: str = ""

    @property
    def should_skip_rag(self) -> bool:
        """この分類結果でRAG検索を省略してよいか返す。"""
        return (
            self.route == "direct"
            and self.response_type in DIRECT_RESPONSE_TYPES
            and self.confidence >= ROUTER_CONFIDENCE_THRESHOLD
        )


def default_rag_decision(reason: str, raw_text: str = "") -> RouteDecision:
    """安全側フォールバックとしてRAG検索を選ぶ。"""
    return RouteDecision(
        route="rag",
        response_type="knowledge",
        confidence=0.0,
        reason=reason,
        raw_text=raw_text,
    )


def normalize_route(value: object) -> str:
    """ルーターJSON内のrouteを正規化する。"""
    route = str(value or "").strip().lower()
    return route if route in VALID_ROUTES else "rag"


def normalize_router_response_type(value: object, route: str) -> str:
    """ルーターJSON内のresponse_typeを正規化する。"""
    response_type = str(value or "").strip().lower()
    if response_type in VALID_ROUTER_RESPONSE_TYPES:
        if route == "direct" and response_type not in DIRECT_RESPONSE_TYPES:
            return "knowledge"
        return response_type
    return "chat" if route == "direct" else "knowledge"


def normalize_confidence(value: object) -> float:
    """ルーターJSON内のconfidenceを0.0から1.0へ収める。"""
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(confidence, 1.0))


def build_router_prompt(request_text: str, current_question: str) -> str:
    """ルーターへ渡す短い分類プロンプトを組み立てる。"""
    return f"""
【指示】
{ROUTER_PROMPT}
【出力形式】
{ROUTER_JSON_INSTRUCTION}
【現在の質問】
{current_question}
【履歴込み入力】
{request_text}
""".strip()


def build_direct_route_context(decision: RouteDecision) -> str:
    """direct回答時に生成モデルへ渡す分類補足を作る。"""
    return (
        f"事前分類は {decision.response_type} です。"
        "RAG検索は不要な会話として処理します。"
        "施設、場所、制度、時間、貸出物などの事実は断定しないでください。"
    )


def route_user_request(request_text: str, current_question: str) -> RouteDecision:
    """質問を分類し、RAG検索が必要か判断する。"""
    if not ROUTER_ENABLED:
        return default_rag_decision("router disabled")

    prompt_text = build_router_prompt(request_text, current_question)
    raw_text = call_gemini_api(prompt_text, model=ROUTER_MODEL)

    try:
        data = extract_json_object(raw_text)
    except Exception:
        return default_rag_decision("router parse failed", raw_text)

    route = normalize_route(data.get("route"))
    response_type = normalize_router_response_type(data.get("response_type"), route)
    confidence = normalize_confidence(data.get("confidence"))
    reason = str(data.get("reason") or "").strip() or "no reason"

    decision = RouteDecision(
        route=route,
        response_type=response_type,
        confidence=confidence,
        reason=reason,
        raw_text=raw_text,
    )
    if route == "direct" and not decision.should_skip_rag:
        return default_rag_decision(
            f"direct rejected: confidence={confidence}, type={response_type}",
            raw_text,
        )
    return decision
