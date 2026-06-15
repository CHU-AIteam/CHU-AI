"""HTTP入出力で使うPydanticスキーマ定義。

API境界のデータ構造を明確にし、
バリデーション以外の処理ロジックは持たない。
"""

from pydantic import BaseModel, Field, field_validator
from typing import Literal

from chu_ai.config import KNOWLEDGE_MODE_DEFAULT

ResponseType = Literal["chat", "knowledge", "unknown", "clarify", "usage"]
"""回答の扱いを決める分類。"""


class ChatRequest(BaseModel):
    """フロントエンドから受け取る質問"""

    text: str
    knowledge_mode: str | None = None


class ChatResponse(BaseModel):
    """フロントエンドへ返す回答"""

    answer: str
    can_answer: bool = True
    response_type: ResponseType = "knowledge"
    recommended_questions: list[str] = Field(default_factory=list)
    used_files: list[str] = Field(default_factory=list)
    knowledge_mode: str = KNOWLEDGE_MODE_DEFAULT
    chat_log_id: str | None = None


FeedbackType = Literal[
    "helpful",
    "knowledge_missing",
    "wrong_answer",
    "hard_to_understand",
    "knowledge_request",
    "other",
]
"""フィードバック種別。"""


class FeedbackRequest(BaseModel):
    """回答1件に対する感想・知識不足フィードバック"""

    chat_log_id: str | int
    helpful: bool
    feedback_type: FeedbackType
    comment: str | None = Field(default=None, max_length=500)

    @field_validator("chat_log_id", mode="before")
    @classmethod
    def normalize_chat_log_id(cls, value: object) -> str:
        """Postgresの数値IDとSheetsの文字列IDを同じAPIで受ける。"""
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("chat_log_id is required")
        if len(normalized) > 80:
            raise ValueError("chat_log_id is too long")
        return normalized


class FeedbackResponse(BaseModel):
    """フィードバック保存結果"""

    ok: bool = True
    feedback_id: str


class ChatLogConversationItem(BaseModel):
    """過去会話1往復分"""

    user: str
    bot: str


class ChatLogItem(BaseModel):
    """管理画面向けチャットログ1件"""

    id: str
    asked_at: str
    question: str
    answer: str
    can_answer: bool
    used_files: list[str] = Field(default_factory=list)
    used_conversation: list[ChatLogConversationItem] = Field(default_factory=list)
    recommended_questions: list[str] = Field(default_factory=list)
    knowledge_mode: str
    request_text: str
    error_type: str | None = None
    error_message: str | None = None


class ChatLogListResponse(BaseModel):
    """チャットログ一覧レスポンス"""

    total: int
    limit: int
    offset: int
    items: list[ChatLogItem] = Field(default_factory=list)


class FeedbackLogItem(BaseModel):
    """管理画面向けフィードバック1件"""

    id: str
    chat_log_id: str
    helpful: bool
    feedback_type: FeedbackType
    comment: str | None = None
    created_at: str
    asked_at: str
    question: str
    answer: str
    knowledge_mode: str
    used_files: list[str] = Field(default_factory=list)


class FeedbackLogListResponse(BaseModel):
    """フィードバック一覧レスポンス"""

    total: int
    limit: int
    offset: int
    items: list[FeedbackLogItem] = Field(default_factory=list)
