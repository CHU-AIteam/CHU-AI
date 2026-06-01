"""チャット1件のユースケースを束ねるサービス。

モード正規化、知識取得、生成処理の呼び出し順を管理し、
HTTP詳細やDB保存の責務は持たない。
"""

from chu_ai.services.chat_log_service import (
    compact_log_text,
    count_history_entries_for_log,
    extract_current_question_for_log,
)
from chu_ai.services.generation_service import build_generation_result, generate_text
from chu_ai.services.knowledge_service import search_knowledge


FORCED_KNOWLEDGE_MODE = "search"
"""クライアント指定に関係なく、常に使うナレッジモード。"""


def process_user_request(
    request_text: str, knowledge_mode: str | None
) -> tuple[dict, list[str], str]:
    """ユーザ質問を処理して生成結果を返す関数"""
    normalized_mode = FORCED_KNOWLEDGE_MODE
    current_question = extract_current_question_for_log(request_text)
    history_count = count_history_entries_for_log(request_text)

    print("Chat request:")
    print(f"  Question: {compact_log_text(current_question)}")
    print(f"  History:  {history_count} exchanges")
    print(f"  Mode:     {normalized_mode}")
    if knowledge_mode and knowledge_mode.strip().lower() != FORCED_KNOWLEDGE_MODE:
        print(
            f"  Mode override: client={knowledge_mode.strip().lower()} "
            f"-> forced={FORCED_KNOWLEDGE_MODE}"
        )

    knowledge_text, used_files = search_knowledge(request_text, normalized_mode)
    raw_result_text = generate_text(knowledge_text, request_text)
    generation = build_generation_result(raw_result_text, current_question)
    return generation, used_files, normalized_mode
