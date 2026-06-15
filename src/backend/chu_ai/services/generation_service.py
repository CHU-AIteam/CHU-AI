"""Gemini呼び出しと生成結果整形を担当するサービス。

プロンプト構築、API実行、JSON正規化を行い、
知識ファイル選定はknowledge_serviceに委譲する。
"""

import json
import os
import re

from chu_ai.config import GEMINI_MODEL
from chu_ai.prompts import AI_prompt, RESPONSE_JSON_INSTRUCTION, chara_personality
from chu_ai.services.chat_log_service import (
    build_gemini_prompt_for_log,
    compact_log_text,
    extract_current_question_for_log,
)


DEFAULT_RECOMMENDED_QUESTIONS = [
    "勉強場所は？",
    "トイレどこ？",
    "自販機どこ？",
    "不言実行館は？",
    "借りられる物は？",
]
"""Gemini出力が不正な時に使う既定のおすすめ質問"""

VALID_RESPONSE_TYPES = {"chat", "knowledge", "unknown", "clarify", "usage"}
"""フロントエンドに返してよい回答分類"""


def normalize_bool(value, fallback: bool = False) -> bool:
    """Gemini JSON内の真偽値をboolへ正規化する関数"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
    return fallback


def normalize_recommended_questions(
    value: object, current_question: str
) -> list[str]:
    """おすすめ質問を最大3件の文字列リストへ正規化する関数"""
    questions = []
    source = value if isinstance(value, list) else []

    for item in source:
        question = str(item).strip()
        if not question or question == current_question or question in questions:
            continue
        questions.append(question)
        if len(questions) >= 3:
            return questions

    for question in DEFAULT_RECOMMENDED_QUESTIONS:
        if question == current_question or question in questions:
            continue
        questions.append(question)
        if len(questions) >= 3:
            break

    return questions


def extract_json_object(text: str) -> dict:
    """Gemini出力からJSONオブジェクト部分だけを取り出す関数"""
    stripped = text.strip()
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"\s*```$", "", stripped)

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or start > end:
        raise ValueError("JSON object was not found")

    return json.loads(stripped[start : end + 1])


def detect_error_type(text: str) -> tuple[str | None, str | None]:
    """生成結果からAPIエラー種別を推定する関数"""
    if "RESOURCE_EXHAUSTED" in text or "429" in text:
        return "gemini_resource_exhausted", text
    if "API_KEY" in text:
        return "api_key_error", text
    if "Gemini SDK" in text:
        return "dependency_error", text
    if text.startswith("エラーが発生しただよ"):
        return "gemini_error", text
    return None, None


def guess_can_answer(answer: str) -> bool:
    """JSONが崩れた場合に回答可否を文章から推定する関数"""
    unknown_markers = [
        "わからない",
        "分からない",
        "今ある情報では",
        "ちょっとわからない",
        "エラーが発生",
        "RESOURCE_EXHAUSTED",
        "API_KEY",
        "Gemini SDK",
    ]
    return bool(answer.strip()) and not any(marker in answer for marker in unknown_markers)


def normalize_response_type(value: object, can_answer: bool) -> str:
    """Gemini JSON内の回答分類を画面制御用の文字列へ正規化する関数"""
    response_type = str(value or "").strip().lower()
    if response_type in VALID_RESPONSE_TYPES:
        if response_type == "knowledge" and not can_answer:
            return "unknown"
        return response_type
    if not can_answer:
        return "unknown"
    return "knowledge"


def normalize_can_answer_for_type(can_answer: bool, response_type: str) -> bool:
    """回答分類と回答可否が矛盾した時に安全側へ寄せる関数"""
    if response_type == "unknown":
        return False
    if response_type in {"chat", "clarify", "usage"}:
        return True
    return can_answer


def build_generation_result(raw_text: str, current_question: str) -> dict:
    """GeminiのJSON出力を画面表示用データへ変換する関数"""
    fallback_answer = (
        raw_text.strip()
        or "回答を生成できなかっただよ。時間をおいてもう一度試してね。"
    )
    error_type, error_message = detect_error_type(fallback_answer)
    fallback_can_answer = error_type is None and guess_can_answer(fallback_answer)

    try:
        data = extract_json_object(fallback_answer)
    except Exception:
        return {
            "answer": fallback_answer,
            "can_answer": fallback_can_answer,
            "response_type": normalize_response_type(None, fallback_can_answer),
            "recommended_questions": []
            if error_type is not None
            else normalize_recommended_questions([], current_question),
            "raw_text": raw_text,
            "error_type": error_type,
            "error_message": error_message,
        }

    answer = str(data.get("answer") or fallback_answer).strip()
    if not answer:
        answer = fallback_answer

    can_answer = normalize_bool(data.get("can_answer"), fallback_can_answer)
    if error_type is not None:
        can_answer = False
    response_type = normalize_response_type(data.get("response_type"), can_answer)
    can_answer = normalize_can_answer_for_type(can_answer, response_type)

    return {
        "answer": answer,
        "can_answer": can_answer,
        "response_type": response_type,
        "recommended_questions": normalize_recommended_questions(
            data.get("recommended_questions"),
            current_question,
        ),
        "raw_text": raw_text,
        "error_type": error_type,
        "error_message": error_message,
    }


def build_generation_prompt(
    knowledge_text: str,
    user_text: str,
    route_context: str = "",
) -> str:
    """Geminiへ渡すプロンプトを組み立てる関数"""
    route_section = f"\n【事前分類】\n{route_context}" if route_context else ""
    return f"""
【指示】
{AI_prompt}
【出力形式】
{RESPONSE_JSON_INSTRUCTION}
【性格】
{chara_personality}
{route_section}
【ナレッジ】
{knowledge_text}
【質問】
{user_text}
"""


def api_key_is_valid(api_key: str) -> bool:
    """Gemini APIキーとして最低限使える文字列か確認する関数"""
    try:
        api_key.encode("ascii")
    except UnicodeEncodeError:
        return False

    return api_key.startswith("AIza") and len(api_key) >= 30


def call_gemini_api(prompt_text: str, model: str = GEMINI_MODEL) -> str:
    """Geminiを呼び出して回答文を返す関数"""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return "Gemini SDKが入っていません。`python3.11 -m pip install -r src/backend/requirements.txt` を実行してください。"

    api_key = os.getenv("API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        return "API_KEYが設定されていません。Google AI Studioで取得したAPIキーを環境変数に設定してください。"
    if not api_key_is_valid(api_key):
        return "API_KEYに実際のGemini APIキーを設定してください。説明文や日本語の文字列ではなく、Google AI Studioで発行されたキーが必要です。"

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model,
            contents=prompt_text,
            config=types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(thinking_budget=0)
            ),
        )
        return response.text
    except Exception as error:
        return f"エラーが発生しただよ...: {error}"


def generate_text(
    knowledge_text: str,
    user_text: str,
    route_context: str = "",
) -> str:
    """ナレッジと質問からGemini回答文を生成する関数"""
    prompt_text = build_generation_prompt(knowledge_text, user_text, route_context)
    current_question = extract_current_question_for_log(user_text)

    print("Generation request:")
    print(f"  Question:     {compact_log_text(current_question)}")
    print(f"  Prompt chars: {len(prompt_text)}")
    print("Gemini prompt (knowledge redacted):")
    print("----- prompt begin -----")
    print(build_gemini_prompt_for_log(user_text))
    print("----- prompt end -----")

    return call_gemini_api(prompt_text)
