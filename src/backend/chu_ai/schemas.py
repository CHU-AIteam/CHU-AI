"""HTTP入出力で使うPydanticスキーマ定義。

API境界のデータ構造を明確にし、
バリデーション以外の処理ロジックは持たない。
"""

from pydantic import BaseModel, Field

from chu_ai.config import KNOWLEDGE_MODE_DEFAULT


class ChatRequest(BaseModel):
    """フロントエンドから受け取る質問"""

    text: str
    knowledge_mode: str | None = None


class ChatResponse(BaseModel):
    """フロントエンドへ返す回答"""

    answer: str
    can_answer: bool = True
    recommended_questions: list[str] = Field(default_factory=list)
    used_files: list[str] = Field(default_factory=list)
    knowledge_mode: str = KNOWLEDGE_MODE_DEFAULT
