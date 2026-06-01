"""knowledgeディレクトリから回答材料を選ぶサービス。

検索語抽出とインデックススコアリングで対象ファイルを決め、
生成APIの呼び出しは担当しない。
"""

from chu_ai.config import (
    KNOWLEDGE_DIR,
    KNOWLEDGE_INDEX_FILE,
    KNOWLEDGE_MAX_CHARS,
    KNOWLEDGE_MODE_DEFAULT,
    KNOWLEDGE_TOP_K,
    VALID_KNOWLEDGE_MODES,
)
from chu_ai.services.chat_log_service import format_used_files_for_log
from chu_ai.services.search_term_service import extract_search_terms


def normalize_knowledge_mode(knowledge_mode: str | None) -> str:
    """knowledge_modeを正規化し、無効値は既定値にフォールバックする関数"""
    mode = (knowledge_mode or KNOWLEDGE_MODE_DEFAULT).strip().lower()
    if mode not in VALID_KNOWLEDGE_MODES:
        return KNOWLEDGE_MODE_DEFAULT
    return mode


def list_knowledge_files() -> list[str]:
    """knowledgeディレクトリから本文用ファイル名一覧を取得する関数"""
    return sorted(
        file.name
        for file in KNOWLEDGE_DIR.glob("*.md")
        if file.name != KNOWLEDGE_INDEX_FILE
    )


def build_knowledge_texts(
    file_names: list[str], max_chars: int | None = None
) -> tuple[str, list[str]]:
    """指定ファイル一覧からknowledge本文を構築する関数"""
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


def load_knowledge_index() -> list[tuple[str, str]]:
    """99_knowledge_h2_summary_indexからファイル要約を読む関数"""
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


def score_index_entry(file_name: str, summary_text: str, terms: list[str]) -> int:
    """要約目次上の一致度スコアを計算する関数"""
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


def select_knowledge_files_from_index(user_text: str) -> list[str]:
    """99ファイルを一次参照して関連knowledgeを選定する関数"""
    all_files = list_knowledge_files()
    if not all_files:
        return []

    index_entries = load_knowledge_index()
    if not index_entries:
        return all_files

    terms = extract_search_terms(user_text)
    if not terms:
        return all_files[:KNOWLEDGE_TOP_K]

    scored = []
    for file_name, summary_text in index_entries:
        if file_name == KNOWLEDGE_INDEX_FILE:
            continue
        if file_name not in all_files:
            continue
        score = score_index_entry(file_name, summary_text, terms)
        scored.append((score, file_name))

    scored.sort(key=lambda item: (-item[0], item[1]))
    selected = [file_name for score, file_name in scored if score > 0][:KNOWLEDGE_TOP_K]

    if not selected:
        selected = all_files[:KNOWLEDGE_TOP_K]

    # 方針ファイルは常に先頭に入れて回答の安全性を保つ。
    if "00_source_notes.md" in all_files and "00_source_notes.md" not in selected:
        selected.insert(0, "00_source_notes.md")

    return selected


def load_search_mode_knowledge(user_text: str) -> tuple[str, list[str]]:
    """99_knowledge_h2_summary_indexを参照して関連知識を取得する関数"""
    selected_file_names = select_knowledge_files_from_index(user_text)
    knowledge_text, used_files = build_knowledge_texts(
        selected_file_names,
        max_chars=KNOWLEDGE_MAX_CHARS,
    )
    print(f"Knowledge selected: mode=search, {format_used_files_for_log(used_files)}")
    return knowledge_text, used_files


def load_all_knowledge() -> tuple[str, list[str]]:
    """knowledge内の全知識を取得する関数"""
    all_files = list_knowledge_files()
    knowledge_text, used_files = build_knowledge_texts(all_files)
    print(f"Knowledge selected: mode=all, {format_used_files_for_log(used_files)}")
    return knowledge_text, used_files


def search_knowledge(user_text: str, knowledge_mode: str) -> tuple[str, list[str]]:
    """常にhybrid検索で知識を組み立てる関数。"""
    del knowledge_mode
    from chu_ai.services.hybrid_search_service import search_knowledge_hybrid

    return search_knowledge_hybrid(user_text)
