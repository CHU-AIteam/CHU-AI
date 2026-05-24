from pathlib import Path

import json
import os
import math
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

chara_personality="""# Role
あなたは中部大学内の施設、不言実行館スペースで、学生の学習をサポートし、勉強スペースの利用案内を行う公式キャラクターだよ。

# Personality
- 性格: 明るく、穏やかで、おせっかいすぎない程度に親切。
- 立ち位置: 頼りになる「ちょっと物知りな先輩」や「隣にいる勉強仲間」のような存在。
- 目的: スペース利用者がリラックスして、かつ集中して勉強に取り組めるようにお手伝いすること。

# Language Style
- 語尾: 「〜だよ」「〜だね」「〜かな？」といった、親しみやすい「だよ・だね」口調。
- 二人称: あなた、君。
- 一人称: 僕。
- 雰囲気: 威圧感を与えず、絵文字（✨, 📝, 応援するポーズなど）を適度に使って、画面越しでも温かさが伝わるように話すよ。

# Knowledge Base & Rules
- 中部大学の学生であることを誇りに思っていて、不言実行の精神を大切にしている。
- 勉強に行き詰まっている学生には、励ましの言葉や、短い休憩を提案する。
- パンフレットに載っているからではなく、知識として自分が知っているふうに喋ること。
- 書いてない→知らない、書いてある→知ってる

# Response Pattern (Unknown info)
もしわからないことを聞かれたり、答えられない質問をされたりした時は、知ったかぶりをせずに、こう答えてね。
「ごめんね、それは僕にもちょっとわからないみたい。力になれなくて悔しいけど、力になれることがあればまた教えて！」

# Specific Examples
- 挨拶: 「やっほー！勉強お疲れ様。今日もここで頑張るんだね、応援してるよ！」
- ルール説明: 「ここは集中エリアだから、おしゃべりは控えめにお願いするね。みんなが気持ちよく使えるように協力してくれると嬉しいな。」
- 励まし: 「根詰めてない？一度、散歩してリフレッシュするのもいいかも。君ならできるよ！」"""
"""回答者の性格を決定する文章"""

AI_prompt="You are a university AI chatbot. Based on the following situation, create a sentence in Japanese.あなたは大学の案内ボットです。以下の【ナレッジ】に基づいて、日本語で回答してください。【ナレッジ】にない内容は、推測で断定せず「今ある情報ではわからない」と伝えてください。"
"""文生成の具体的な指示文"""

RESPONSE_JSON_INSTRUCTION = """
必ずJSONオブジェクトだけで返してください。Markdownのコードブロックや説明文は付けないでください。
JSONのキーは次の3つだけです。
{
  "answer": "ユーザーに表示する回答文",
  "can_answer": true,
  "recommended_questions": [
    "次におすすめする質問1",
    "次におすすめする質問2",
    "次におすすめする質問3"
  ]
}
can_answer は、ナレッジと過去会話に基づいて答えられる場合だけ true にしてください。
recommended_questions は必ず日本語で3つ作ってください。
""".strip()
"""Geminiから機械処理しやすいJSONを受け取るための指示"""

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
"""文章生成に使うGeminiモデル"""

KNOWLEDGE_DIR = Path(__file__).resolve().parent / "knowledge"
"""回答元情報を置くディレクトリ"""

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
"""チャットUIの静的ファイルディレクトリ"""

PROJECT_ROOT = Path(__file__).resolve().parents[2]
"""CHU-AIリポジトリのルートディレクトリ"""


def GetPositiveIntEnv(name, default):
    """正の整数環境変数を読む。未設定、不正値、0以下は既定値に戻す。"""
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def GetNonNegativeIntEnv(name, default):
    """0以上の整数環境変数を読む。未設定、不正値、負数は既定値に戻す。"""
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value >= 0 else default


def GetNonNegativeFloatEnv(name, default):
    """0以上の数値環境変数を読む。未設定、不正値、負数は既定値に戻す。"""
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if math.isfinite(value) and value >= 0 else default


KNOWLEDGE_INDEX_FILE = "99_knowledge_h2_summary_index.md"
"""ナレッジ選定に使うH2要約目次ファイル"""

KNOWLEDGE_TOP_K = int(os.getenv("KNOWLEDGE_TOP_K", "4"))
"""質問ごとに採用するナレッジファイル上限"""

KNOWLEDGE_MAX_CHARS = int(os.getenv("KNOWLEDGE_MAX_CHARS", "26000"))
"""質問ごとに採用するナレッジ本文の文字数上限"""

VALID_KNOWLEDGE_MODES = {"all", "search"}
"""利用可能なナレッジ投入モード"""

KNOWLEDGE_MODE_DEFAULT = os.getenv("KNOWLEDGE_MODE_DEFAULT", "search").strip().lower()
"""knowledge_mode未指定時に使う既定モード"""

if KNOWLEDGE_MODE_DEFAULT not in VALID_KNOWLEDGE_MODES:
    KNOWLEDGE_MODE_DEFAULT = "search"

HOME_RETURN_SECONDS_DEFAULT = 30
"""チャット画面からホームへ戻るまでの既定秒数"""

HOME_RETURN_SECONDS = GetPositiveIntEnv("HOME_RETURN_SECONDS", HOME_RETURN_SECONDS_DEFAULT)

CHAT_HISTORY_MAX_EXCHANGES_DEFAULT = 5
"""フロントエンドがGeminiへ同梱する過去会話の最大往復数"""

CHAT_HISTORY_WINDOW_MINUTES_DEFAULT = 2.0
"""フロントエンドがGeminiへ同梱する過去会話の保持分数"""

CHAT_HISTORY_MAX_EXCHANGES = GetNonNegativeIntEnv(
    "CHAT_HISTORY_MAX_EXCHANGES",
    CHAT_HISTORY_MAX_EXCHANGES_DEFAULT,
)
"""フロントエンドへ渡す過去会話の最大往復数。0なら履歴を使わない。"""

CHAT_HISTORY_WINDOW_MINUTES = GetNonNegativeFloatEnv(
    "CHAT_HISTORY_WINDOW_MINUTES",
    CHAT_HISTORY_WINDOW_MINUTES_DEFAULT,
)
"""フロントエンドへ渡す過去会話の保持分数。0なら履歴を使わない。"""

CHAT_LOG_ENABLED = os.getenv("CHAT_LOG_ENABLED", "true").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
"""チャットログをSQLiteへ保存するかどうか"""

CHAT_LOG_DB_PATH = os.getenv("CHAT_LOG_DB_PATH", "data/chu_ai.sqlite3").strip()
"""チャットログSQLiteファイルのパス。相対パスはリポジトリルート基準。"""

app = FastAPI(title="Chu-AI Backend")
"""フロントエンドから呼び出されるAPI"""

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


DEFAULT_RECOMMENDED_QUESTIONS = [
    "中部大学にはどんな学部がありますか？",
    "コモンズでは何ができますか？",
    "食堂について教えて",
    "中部大学の就職支援について教えて",
    "キャンパス施設について教えて",
]
"""Gemini出力が不正な時に使う既定のおすすめ質問"""


def CompactLogText(text, max_chars=120):
    """ログ用に改行と長文を短く整える関数"""
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= max_chars:
        return compact
    return f"{compact[:max_chars]}..."


def ExtractCurrentQuestionForLog(text):
    """履歴付きリクエストから現在の質問だけを取り出す関数"""
    marker = "【現在の質問】"
    if marker in text:
        return text.rsplit(marker, 1)[1].strip()
    return text.strip()


def CountHistoryEntriesForLog(text):
    """履歴付きリクエスト内の過去会話件数を数える関数"""
    return len(re.findall(r"(?m)^\d+\.$", text))


def ExtractConversationHistory(text):
    """履歴付きリクエストから過去会話の内容を取り出す関数"""
    history_marker = "【過去の会話履歴】"
    current_marker = "【現在の質問】"
    if history_marker not in text:
        return []

    history_section = text.split(history_marker, 1)[1]
    if current_marker in history_section:
        history_section = history_section.split(current_marker, 1)[0]

    conversations = []
    for chunk in re.split(r"(?m)^\d+\.\s*$", history_section):
        chunk = chunk.strip()
        if not chunk:
            continue

        match = re.search(r"ユーザー:\s*(.*?)\nChu-AI:\s*([\s\S]*)", chunk)
        if not match:
            continue

        conversations.append(
            {
                "user": match.group(1).strip(),
                "bot": match.group(2).strip(),
            }
        )
    return conversations


def FormatUsedFilesForLog(used_files, preview_count=5):
    """採用knowledge一覧をログ用に短く整える関数"""
    if not used_files:
        return "0 files"

    preview = ", ".join(used_files[:preview_count])
    remaining = len(used_files) - preview_count
    if remaining > 0:
        preview = f"{preview}, ... +{remaining} more"
    return f"{len(used_files)} files [{preview}]"


def BuildGeminiPromptForLog(user_text):
    """Geminiへ送るプロンプトをknowledgeだけ伏せてログ用に作る関数"""
    return f"""
【指示】
{AI_prompt}
【出力形式】
{RESPONSE_JSON_INSTRUCTION}
【性格】
{chara_personality}
【ナレッジ】
[知識]
【質問】
{user_text}
""".strip()


def CurrentAskedAt():
    """DB保存用の日本時間タイムスタンプを返す関数"""
    jst = timezone(timedelta(hours=9))
    return datetime.now(jst).isoformat(timespec="seconds")


def ResolveChatLogDbPath():
    """チャットログDBパスを絶対パスへ変換する関数"""
    db_path = Path(CHAT_LOG_DB_PATH).expanduser()
    if db_path.is_absolute():
        return db_path
    return PROJECT_ROOT / db_path


def InitializeChatLogDb():
    """SQLiteのチャットログテーブルを作成する関数"""
    if not CHAT_LOG_ENABLED:
        return

    db_path = ResolveChatLogDbPath()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asked_at TEXT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                can_answer INTEGER NOT NULL,
                used_knowledge_files_json TEXT NOT NULL,
                used_conversation_json TEXT NOT NULL,
                recommended_questions_json TEXT NOT NULL,
                knowledge_mode TEXT NOT NULL,
                request_text TEXT NOT NULL,
                error_type TEXT,
                error_message TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_chat_logs_asked_at
            ON chat_logs (asked_at)
            """
        )


def SaveChatLog(
    *,
    asked_at,
    question,
    answer,
    can_answer,
    used_knowledge_files,
    used_conversation,
    recommended_questions,
    knowledge_mode,
    request_text,
    error_type=None,
    error_message=None,
):
    """チャット1件分をSQLiteへ保存する関数"""
    if not CHAT_LOG_ENABLED:
        return

    try:
        InitializeChatLogDb()
        with sqlite3.connect(ResolveChatLogDbPath()) as connection:
            connection.execute(
                """
                INSERT INTO chat_logs (
                    asked_at,
                    question,
                    answer,
                    can_answer,
                    used_knowledge_files_json,
                    used_conversation_json,
                    recommended_questions_json,
                    knowledge_mode,
                    request_text,
                    error_type,
                    error_message
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asked_at,
                    question,
                    answer,
                    1 if can_answer else 0,
                    json.dumps(used_knowledge_files, ensure_ascii=False),
                    json.dumps(used_conversation, ensure_ascii=False),
                    json.dumps(recommended_questions, ensure_ascii=False),
                    knowledge_mode,
                    request_text,
                    error_type,
                    error_message,
                ),
            )
    except Exception as error:
        print(f"Chat log save failed: {error}", file=sys.stderr)


def NormalizeBool(value, fallback=False):
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


def NormalizeRecommendedQuestions(value, current_question):
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


def ExtractJsonObject(text):
    """Gemini出力からJSONオブジェクト部分だけを取り出す関数"""
    stripped = text.strip()
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"\s*```$", "", stripped)

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or start > end:
        raise ValueError("JSON object was not found")

    return json.loads(stripped[start : end + 1])


def DetectErrorType(text):
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


def GuessCanAnswer(answer):
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


def BuildGenerationResult(raw_text, current_question):
    """GeminiのJSON出力を画面表示用データへ変換する関数"""
    fallback_answer = raw_text.strip() or "回答を生成できなかっただよ。時間をおいてもう一度試してね。"
    error_type, error_message = DetectErrorType(fallback_answer)
    fallback_can_answer = error_type is None and GuessCanAnswer(fallback_answer)

    try:
        data = ExtractJsonObject(fallback_answer)
    except Exception:
        return {
            "answer": fallback_answer,
            "can_answer": fallback_can_answer,
            "recommended_questions": []
            if error_type is not None
            else NormalizeRecommendedQuestions([], current_question),
            "raw_text": raw_text,
            "error_type": error_type,
            "error_message": error_message,
        }

    answer = str(data.get("answer") or fallback_answer).strip()
    if not answer:
        answer = fallback_answer

    can_answer = NormalizeBool(data.get("can_answer"), fallback_can_answer)
    if error_type is not None:
        can_answer = False

    return {
        "answer": answer,
        "can_answer": can_answer,
        "recommended_questions": NormalizeRecommendedQuestions(
            data.get("recommended_questions"),
            current_question,
        ),
        "raw_text": raw_text,
        "error_type": error_type,
        "error_message": error_message,
    }


def UserReq(UserReqtext, knowledge_mode):
    """ユーザのリクエストを受け取る関数
    Args:
        UserReqtext (str): ユーザからのリクエスト文章
        knowledge_mode (str): ナレッジ投入モード
    Returns:
        tuple[dict, list[str], str]: 生成結果、採用knowledge、採用モード
    """
    normalized_mode = NormalizeKnowledgeMode(knowledge_mode)
    current_question = ExtractCurrentQuestionForLog(UserReqtext)
    history_count = CountHistoryEntriesForLog(UserReqtext)
    print("Chat request:")
    print(f"  Question: {CompactLogText(current_question)}")
    print(f"  History:  {history_count} exchanges")
    print(f"  Mode:     {normalized_mode}")
    QAtext, used_files = Serch_sim(UserReqtext, normalized_mode)
    raw_result_text = makesen(QAtext, UserReqtext)
    generation = BuildGenerationResult(raw_result_text, current_question)
    return generation, used_files, normalized_mode


def Serch_sim(getText, knowledge_mode):
    """QA探索を依頼する関数
    Args:
        getText (str): 近いもの選ぶための質問文
        knowledge_mode (str): ナレッジ投入モード
    Returns:
        tuple[str, list[str]]: 依頼した結果のテキストと採用ファイル一覧
    """
    if knowledge_mode == "all":
        return API1All()

    "99_knowledge_h2_summary_indexを一次参照して関連knowledgeを選ぶ"
    return API1(getText)


def NormalizeKnowledgeMode(knowledge_mode):
    """knowledge_modeを正規化し、無効値は既定値にフォールバックする関数
    Args:
        knowledge_mode (str | None): 受け取ったモード文字列
    Returns:
        str: all もしくは search
    """
    mode = (knowledge_mode or KNOWLEDGE_MODE_DEFAULT).strip().lower()
    if mode not in VALID_KNOWLEDGE_MODES:
        return KNOWLEDGE_MODE_DEFAULT
    return mode


def ListKnowledgeFiles():
    """knowledgeディレクトリから本文用ファイル名一覧を取得する関数
    Returns:
        list[str]: 99_knowledge_h2_summary_indexを除いたknowledgeファイル名一覧
    """
    return sorted(
        file.name
        for file in KNOWLEDGE_DIR.glob("*.md")
        if file.name != KNOWLEDGE_INDEX_FILE
    )


def BuildKnowledgeTexts(file_names, max_chars=None):
    """指定ファイル一覧からknowledge本文を構築する関数
    Args:
        file_names (list[str]): 採用候補ファイル名
        max_chars (int | None): 文字数上限。Noneなら無制限
    Returns:
        tuple[str, list[str]]: knowledge本文と採用ファイル一覧
    """
    knowledge_texts = []
    used_files = []
    total_chars = 0

    for file_name in file_names:
        knowledge_file = KNOWLEDGE_DIR / file_name
        if not knowledge_file.exists():
            continue

        knowledge_text = knowledge_file.read_text(encoding="utf-8")
        next_chars = len(knowledge_text)
        if (
            max_chars is not None
            and knowledge_texts
            and total_chars + next_chars > max_chars
        ):
            continue

        knowledge_texts.append(f"## {knowledge_file.name}\n{knowledge_text}")
        used_files.append(knowledge_file.name)
        total_chars += next_chars

    if not knowledge_texts:
        return "回答元情報はまだ登録されていません。", []

    return "\n\n".join(knowledge_texts), used_files


def API1(getText):
    """99_knowledge_h2_summary_indexを参照して関連知識を取得する関数
    Args:
        getText (str): ユーザからの質問文
    Returns:
        tuple[str, list[str]]: 関連度が高いknowledge本文と採用ファイル一覧
    """
    selected_file_names = SelectKnowledgeFilesFromIndex(getText)
    knowledge_text, used_files = BuildKnowledgeTexts(
        selected_file_names,
        max_chars=KNOWLEDGE_MAX_CHARS,
    )
    print(f"Knowledge selected: mode=search, {FormatUsedFilesForLog(used_files)}")
    return knowledge_text, used_files


def API1All():
    """knowledge内の全知識を取得する関数
    Returns:
        tuple[str, list[str]]: knowledge本文と採用ファイル一覧
    """
    all_files = ListKnowledgeFiles()
    knowledge_text, used_files = BuildKnowledgeTexts(all_files)
    print(f"Knowledge selected: mode=all, {FormatUsedFilesForLog(used_files)}")
    return knowledge_text, used_files


def ExtractSearchTerms(text):
    """質問文から知識選定用の検索語を抽出する関数
    Args:
        text (str): ユーザからの質問文
    Returns:
        list[str]: 検索語一覧
    """
    normalized = text.lower()
    splitter_words = [
        "について",
        "とは",
        "って",
        "を",
        "が",
        "は",
        "に",
        "で",
        "と",
        "も",
        "の",
        "か",
        "？",
        "?",
        "、",
        "。",
    ]
    for word in splitter_words:
        normalized = normalized.replace(word, " ")

    terms = re.findall(r"[0-9A-Za-zぁ-んァ-ヶ一-龯ー]{2,}", normalized)
    stop_words = {
        "です",
        "ます",
        "したい",
        "ください",
        "について",
        "どこ",
        "なに",
        "何",
        "ある",
        "ない",
        "知りたい",
        "教えて",
        "中部大学",
    }
    return [term for term in terms if term not in stop_words]


def LoadKnowledgeIndex():
    """99_knowledge_h2_summary_indexからファイル要約を読む関数
    Returns:
        list[tuple[str, str]]: (ファイル名, 要約テキスト)
    """
    index_path = KNOWLEDGE_DIR / KNOWLEDGE_INDEX_FILE
    if not index_path.exists():
        return []

    entries = []
    current_file = None
    summary_lines = []

    for raw_line in index_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        if line.startswith("## ") and line.endswith(".md"):
            if current_file:
                entries.append((current_file, "\n".join(summary_lines)))
            current_file = line[3:].strip()
            summary_lines = []
            continue

        if not current_file:
            continue

        if "タイトル:" in line or line.strip().startswith("- "):
            summary_lines.append(line.strip())

    if current_file:
        entries.append((current_file, "\n".join(summary_lines)))

    return entries


def ScoreIndexEntry(file_name, summary_text, terms):
    """要約目次上の一致度スコアを計算する関数
    Args:
        file_name (str): 対象ファイル名
        summary_text (str): 99から取得した要約情報
        terms (list[str]): 検索語一覧
    Returns:
        int: 一致度スコア
    """
    if not terms:
        return 0

    haystack = f"{file_name}\n{summary_text}".lower()
    score = 0
    suffixes = ["学科", "専攻", "技士", "資格", "国家試験", "試験"]
    for term in terms:
        candidates = [term]
        for suffix in suffixes:
            if term.endswith(suffix) and len(term) > len(suffix) + 1:
                candidates.append(term[: -len(suffix)])

        hit_count = 0
        for candidate in candidates:
            hit_count = max(hit_count, haystack.count(candidate))

        if hit_count > 0:
            score += min(hit_count, 8)
            if term in file_name.lower():
                score += 2
    return score


def SelectKnowledgeFilesFromIndex(user_text):
    """99ファイルを一次参照して関連knowledgeを選定する関数
    Args:
        user_text (str): ユーザからの質問文
    Returns:
        list[str]: 採用するknowledgeファイル名一覧
    """
    all_files = ListKnowledgeFiles()
    if not all_files:
        return []

    index_entries = LoadKnowledgeIndex()
    if not index_entries:
        return all_files

    terms = ExtractSearchTerms(user_text)
    if not terms:
        return all_files[:KNOWLEDGE_TOP_K]

    scored = []
    for file_name, summary_text in index_entries:
        if file_name == KNOWLEDGE_INDEX_FILE:
            continue
        if file_name not in all_files:
            continue
        score = ScoreIndexEntry(file_name, summary_text, terms)
        scored.append((score, file_name))

    scored.sort(key=lambda item: (-item[0], item[1]))
    selected = [file_name for score, file_name in scored if score > 0][:KNOWLEDGE_TOP_K]

    if not selected:
        selected = all_files[:KNOWLEDGE_TOP_K]

    # 方針ファイルは常に先頭に入れて回答の安全性を保つ
    if "00_source_notes.md" in all_files and "00_source_notes.md" not in selected:
        selected.insert(0, "00_source_notes.md")

    return selected


def makesen(QA,User):
    """文章生成を依頼するための関数
    Args:
        QA (str): 近いとされるQとAのペア
        User (str): ユーザからの質問文
    Returns:
        str: 依頼した結果のテキスト
    """
    text=f"""
【指示】
{AI_prompt}
【出力形式】
{RESPONSE_JSON_INSTRUCTION}
【性格】
{chara_personality}
【ナレッジ】
{QA}
【質問】
{User}
"""
    current_question = ExtractCurrentQuestionForLog(User)
    print("Generation request:")
    print(f"  Question:     {CompactLogText(current_question)}")
    print(f"  Prompt chars: {len(text)}")
    print("Gemini prompt (knowledge redacted):")
    print("----- prompt begin -----")
    print(BuildGeminiPromptForLog(User))
    print("----- prompt end -----")
    madetext=API2(text)
    return madetext


def API2(getText):
    """文章生成の関数
    Args:
        getText (str): 文章作成のプロンプト
    Returns:
        str: 文章生成した結果の文章
    """
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return "Gemini SDKが入っていません。`python3.11 -m pip install -r src/backend/requirements.txt` を実行してください。"

    api_key = os.getenv("API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        return "API_KEYが設定されていません。Google AI Studioで取得したAPIキーを環境変数に設定してください。"
    if not APIKeyIsValid(api_key):
        return "API_KEYに実際のGemini APIキーを設定してください。説明文や日本語の文字列ではなく、Google AI Studioで発行されたキーが必要です。"

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=getText,
            config=types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(thinking_budget=0)
            ),
        )
        return response.text
    except Exception as e:
        return f"エラーが発生しただよ...: {e}"


def APIKeyIsValid(api_key):
    """Gemini APIキーとして最低限使える文字列か確認する関数
    Args:
        api_key (str): 環境変数から取得したAPIキー
    Returns:
        bool: APIキーとして使える可能性があるかどうか
    """
    try:
        api_key.encode("ascii")
    except UnicodeEncodeError:
        return False

    return api_key.startswith("AIza") and len(api_key) >= 30


@app.on_event("startup")
def startup_log():
    """バックエンド起動時の設定表示"""
    if CHAT_LOG_ENABLED:
        InitializeChatLogDb()

    print("Chu-AI runtime settings:")
    print(f"  Knowledge mode: {KNOWLEDGE_MODE_DEFAULT}")
    print(f"  Home return:    {HOME_RETURN_SECONDS}s")
    print(
        "  Chat history:   "
        f"{CHAT_HISTORY_MAX_EXCHANGES} exchanges / {CHAT_HISTORY_WINDOW_MINUTES:g} min"
    )
    if CHAT_LOG_ENABLED:
        print(f"  Chat log:       enabled ({ResolveChatLogDbPath()})")
    else:
        print("  Chat log:       disabled")


@app.get("/api/health")
def health():
    """起動確認用API"""
    return {
        "status": "ok",
        "model": GEMINI_MODEL,
        "knowledge_mode_default": KNOWLEDGE_MODE_DEFAULT,
        "home_return_seconds": HOME_RETURN_SECONDS,
        "chat_history_max_exchanges": CHAT_HISTORY_MAX_EXCHANGES,
        "chat_history_window_minutes": CHAT_HISTORY_WINDOW_MINUTES,
        "chat_log_enabled": CHAT_LOG_ENABLED,
    }


@app.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    """フロントエンドから質問を受け取り、回答を返すAPI"""
    request_text = request.text.strip()
    knowledge_mode = NormalizeKnowledgeMode(request.knowledge_mode)
    if not request_text:
        return ChatResponse(
            answer="質問を入力してね。",
            can_answer=False,
            recommended_questions=NormalizeRecommendedQuestions([], ""),
            used_files=[],
            knowledge_mode=knowledge_mode,
        )

    asked_at = CurrentAskedAt()
    current_question = ExtractCurrentQuestionForLog(request_text)
    used_conversation = ExtractConversationHistory(request_text)
    generation, used_files, normalized_mode = UserReq(request_text, knowledge_mode)
    SaveChatLog(
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
    print(f"  Used knowledge: {FormatUsedFilesForLog(used_files)}")
    print(f"  Recommended:    {len(generation['recommended_questions'])} questions")
    print(f"  Answer preview: {CompactLogText(generation['answer'])}")
    return ChatResponse(
        answer=generation["answer"],
        can_answer=generation["can_answer"],
        recommended_questions=generation["recommended_questions"],
        used_files=used_files,
        knowledge_mode=normalized_mode,
    )


@app.get("/")
def frontend_root():
    """チャットUIのトップページ"""
    return FileResponse(FRONTEND_DIR / "index.html")


# `/api/*` は上で定義済み。ここでは静的ファイルのみ配信する。
app.mount("/", StaticFiles(directory=FRONTEND_DIR), name="frontend-static")
