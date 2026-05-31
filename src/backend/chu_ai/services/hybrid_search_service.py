"""ハイブリッド検索のユースケースサービス。

キーワード検索とベクトル検索をRRFで統合し、
回答生成に渡すknowledge文字列を組み立てる。
"""

from __future__ import annotations

from dataclasses import dataclass

from chu_ai.config import (
    HYBRID_AUTO_INIT_DB,
    HYBRID_FINAL_TOP_K,
    HYBRID_KEYWORD_TOP_K,
    HYBRID_RRF_K,
    HYBRID_VECTOR_TOP_K,
    KNOWLEDGE_MAX_CHARS,
)
from chu_ai.services.chat_log_service import (
    compact_log_text,
    extract_current_question_for_log,
    format_used_files_for_log,
)
from chu_ai.services.embedding_service import embed_query_text
from chu_ai.services.hybrid_store_service import (
    ChunkCandidate,
    has_hybrid_database_config,
    initialize_hybrid_database,
    search_keyword_candidates,
    search_vector_candidates,
)
from chu_ai.services.search_term_service import extract_search_terms


@dataclass
class RankedChunk:
    """RRF統合後の検索チャンク。"""

    candidate: ChunkCandidate
    fused_score: float
    keyword_rank: int | None
    vector_rank: int | None


def initialize_hybrid_search_runtime() -> None:
    """起動時にhybrid検索基盤を初期化する。"""
    if not HYBRID_AUTO_INIT_DB:
        return
    if not has_hybrid_database_config():
        return
    initialize_hybrid_database()


def _build_rrf_ranking(
    keyword_candidates: list[ChunkCandidate],
    vector_candidates: list[ChunkCandidate],
    *,
    rrf_k: int,
) -> list[RankedChunk]:
    """2つの候補リストをRRFで統合し、スコア順に返す。"""
    rank_map: dict[int, RankedChunk] = {}

    for rank, candidate in enumerate(keyword_candidates, start=1):
        fused = 1.0 / (rrf_k + rank)
        existing = rank_map.get(candidate.chunk_id)
        if existing is None:
            rank_map[candidate.chunk_id] = RankedChunk(
                candidate=candidate,
                fused_score=fused,
                keyword_rank=rank,
                vector_rank=None,
            )
        else:
            existing.fused_score += fused
            existing.keyword_rank = rank

    for rank, candidate in enumerate(vector_candidates, start=1):
        fused = 1.0 / (rrf_k + rank)
        existing = rank_map.get(candidate.chunk_id)
        if existing is None:
            rank_map[candidate.chunk_id] = RankedChunk(
                candidate=candidate,
                fused_score=fused,
                keyword_rank=None,
                vector_rank=rank,
            )
        else:
            existing.fused_score += fused
            existing.vector_rank = rank

    ranked = list(rank_map.values())
    ranked.sort(
        key=lambda item: (
            -item.fused_score,
            item.keyword_rank or 10_000,
            item.vector_rank or 10_000,
            item.candidate.chunk_id,
        )
    )
    return ranked


def _build_knowledge_payload(ranked_chunks: list[RankedChunk]) -> tuple[str, list[str]]:
    """採用チャンクからGemini入力用knowledge本文を組み立てる。"""
    knowledge_blocks: list[str] = []
    used_files: list[str] = []
    total_chars = 0

    for ranked in ranked_chunks[:HYBRID_FINAL_TOP_K]:
        candidate = ranked.candidate
        block = f"## {candidate.source_path} / {candidate.section}\n{candidate.body}"
        if knowledge_blocks and total_chars + len(block) > KNOWLEDGE_MAX_CHARS:
            continue

        knowledge_blocks.append(block)
        total_chars += len(block)

        if candidate.source_path not in used_files:
            used_files.append(candidate.source_path)

    if not knowledge_blocks:
        return "回答元情報はまだ登録されていません。", []

    return "\n\n".join(knowledge_blocks), used_files


def search_knowledge_hybrid(user_text: str) -> tuple[str, list[str]]:
    """hybrid検索を実行してknowledge本文を返す。"""
    if not has_hybrid_database_config():
        raise RuntimeError("POSTGRES_DSN が未設定のためhybrid検索を実行できません。")

    current_question = extract_current_question_for_log(user_text)
    query_text = current_question or user_text

    query_terms = extract_search_terms(query_text)
    keyword_candidates = search_keyword_candidates(query_terms, HYBRID_KEYWORD_TOP_K)

    query_embedding = embed_query_text(query_text)
    vector_candidates = search_vector_candidates(
        query_embedding,
        limit=HYBRID_VECTOR_TOP_K,
    )

    ranked_chunks = _build_rrf_ranking(
        keyword_candidates,
        vector_candidates,
        rrf_k=HYBRID_RRF_K,
    )
    knowledge_text, used_files = _build_knowledge_payload(ranked_chunks)

    print("Knowledge selected: mode=hybrid")
    print(f"  Query:         {compact_log_text(query_text)}")
    print(f"  Keyword hits:  {len(keyword_candidates)}")
    print(f"  Vector hits:   {len(vector_candidates)}")
    print(f"  Final chunks:  {min(len(ranked_chunks), HYBRID_FINAL_TOP_K)}")
    print(f"  Used files:    {format_used_files_for_log(used_files)}")
    return knowledge_text, used_files


def search_knowledge_hybrid_with_fallback(user_text: str) -> tuple[str, list[str]]:
    """hybrid検索を試し、失敗時はlegacy検索へフォールバックする。"""
    try:
        knowledge_text, used_files = search_knowledge_hybrid(user_text)
        if used_files:
            return knowledge_text, used_files
        print("Hybrid search returned empty result. Fallback to legacy search.")
    except Exception as error:
        print(f"Hybrid search failed: {error}. Fallback to legacy search.")

    from chu_ai.services.knowledge_service import load_search_mode_knowledge

    return load_search_mode_knowledge(user_text)
