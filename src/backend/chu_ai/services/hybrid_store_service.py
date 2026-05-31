"""PostgreSQL上のハイブリッド検索ストアを扱うサービス。

knowledgeの取り込み、キーワード検索、ベクトル検索を提供し、
APIレスポンス整形やフォールバック判断は担当しない。
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Iterable

from chu_ai.config import (
    HYBRID_CHUNK_OVERLAP,
    HYBRID_CHUNK_SIZE,
    HYBRID_EMBEDDING_DIM,
    HYBRID_EMBEDDING_MODEL,
    KNOWLEDGE_DIR,
    KNOWLEDGE_INDEX_FILE,
    POSTGRES_CONNECT_TIMEOUT_SECONDS,
    POSTGRES_DSN,
)
from chu_ai.services.search_term_service import extract_search_terms


@dataclass(frozen=True)
class ChunkCandidate:
    """検索候補チャンクを表す値オブジェクト。"""

    chunk_id: int
    source_path: str
    section: str
    body: str
    score: float
    search_type: str


@dataclass(frozen=True)
class KnowledgeChunk:
    """インデックス登録対象のチャンク情報。"""

    chunk_no: int
    section: str
    body: str
    search_terms: list[str]


def has_hybrid_database_config() -> bool:
    """hybrid検索に必要なPostgreSQL接続情報が設定されているか返す。"""
    return bool(POSTGRES_DSN)


def _connect_postgres():
    """PostgreSQL接続を返す。psycopg未導入時は例外を送出する。"""
    try:
        import psycopg
    except ImportError as error:
        raise RuntimeError(
            "psycopg が見つかりません。`pip install -r src/backend/requirements.txt` を実行してください。"
        ) from error

    if not POSTGRES_DSN:
        raise RuntimeError("POSTGRES_DSN が未設定です。")

    return psycopg.connect(
        POSTGRES_DSN,
        connect_timeout=POSTGRES_CONNECT_TIMEOUT_SECONDS,
    )


def initialize_hybrid_database() -> None:
    """hybrid検索用テーブルとインデックスを初期化する。"""
    if not has_hybrid_database_config():
        return

    with _connect_postgres() as connection:
        with connection.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id BIGSERIAL PRIMARY KEY,
                    source_path TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    id BIGSERIAL PRIMARY KEY,
                    document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    chunk_no INTEGER NOT NULL,
                    section TEXT NOT NULL,
                    body TEXT NOT NULL,
                    token_len INTEGER NOT NULL,
                    search_terms TEXT[] NOT NULL DEFAULT '{}',
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(document_id, chunk_no)
                )
                """
            )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS chunk_embeddings (
                    chunk_id BIGINT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
                    model TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    dim INTEGER NOT NULL,
                    embedding VECTOR NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY(chunk_id, model)
                )
                """
            )

            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_documents_active
                ON documents (is_active)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chunks_document_id
                ON chunks (document_id, chunk_no)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chunks_search_terms_gin
                ON chunks USING GIN (search_terms)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_model
                ON chunk_embeddings (model)
                """
            )

            # 変長vector列でも検索できるよう、式インデックスで次元を固定する。
            cursor.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_vector_hnsw
                ON chunk_embeddings
                USING hnsw ((embedding::vector({HYBRID_EMBEDDING_DIM})) vector_cosine_ops)
                """
            )


def _list_knowledge_paths() -> list[Path]:
    """knowledge配下の本文Markdownファイル一覧を返す。"""
    return sorted(
        path
        for path in KNOWLEDGE_DIR.glob("*.md")
        if path.name != KNOWLEDGE_INDEX_FILE
    )


def _compute_source_hash(text: str) -> str:
    """knowledge本文の内容ハッシュを返す。"""
    return sha256(text.encode("utf-8")).hexdigest()


def _build_document_title(path: Path, text: str) -> str:
    """ファイル内容から文書タイトルを抽出する。"""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return path.stem


def _build_document_category(path: Path) -> str:
    """ファイル名からざっくりしたカテゴリを作る。"""
    stem = path.stem
    if "_" in stem:
        _, raw = stem.split("_", 1)
        return raw.split("_", 1)[0]
    return "general"


def _split_markdown_sections(text: str) -> list[tuple[str, str]]:
    """Markdown本文を見出し単位で分割する。"""
    lines = text.splitlines()
    sections: list[tuple[str, str]] = []
    current_heading = "本文"
    current_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            if current_lines:
                body = "\n".join(current_lines).strip()
                if body:
                    sections.append((current_heading, body))
            current_heading = stripped[3:].strip() or "本文"
            current_lines = []
            continue
        current_lines.append(line)

    if current_lines:
        body = "\n".join(current_lines).strip()
        if body:
            sections.append((current_heading, body))

    if not sections and text.strip():
        return [("本文", text.strip())]
    return sections


def _chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """文字数ベースで本文を重複付きチャンクへ分割する。"""
    normalized = text.strip()
    if not normalized:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(len(normalized), start + chunk_size)
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(normalized):
            break
        start = max(end - overlap, start + 1)
    return chunks


def build_document_chunks(text: str) -> list[KnowledgeChunk]:
    """Markdown本文からインデックス登録用チャンク一覧を作る。"""
    chunks: list[KnowledgeChunk] = []
    chunk_no = 0
    for section, section_text in _split_markdown_sections(text):
        for body in _chunk_text(section_text, HYBRID_CHUNK_SIZE, HYBRID_CHUNK_OVERLAP):
            chunk_no += 1
            chunks.append(
                KnowledgeChunk(
                    chunk_no=chunk_no,
                    section=section,
                    body=body,
                    search_terms=extract_search_terms(body),
                )
            )
    return chunks


def _to_vector_literal(vector: Iterable[float]) -> str:
    """float配列をpgvector用の文字列表現へ変換する。"""
    values = ",".join(f"{value:.10f}" for value in vector)
    return f"[{values}]"


def _fetch_existing_document(cursor, source_path: str):
    cursor.execute(
        """
        SELECT id, source_hash
        FROM documents
        WHERE source_path = %s
        """,
        (source_path,),
    )
    return cursor.fetchone()


def _deactivate_removed_documents(cursor, source_paths: list[str]) -> int:
    """現在のknowledgeに存在しない文書をinactive化する。"""
    cursor.execute(
        """
        UPDATE documents
        SET is_active = FALSE, updated_at = NOW()
        WHERE source_path <> ALL(%s::text[])
        """,
        (source_paths,),
    )
    return cursor.rowcount


def ingest_knowledge_to_hybrid_store(
    embeddings_by_chunk: dict[str, list[float]],
    *,
    force_rebuild: bool = False,
    model: str = HYBRID_EMBEDDING_MODEL,
) -> dict[str, int]:
    """knowledgeをPostgreSQLへ再取り込みする。

    embeddings_by_chunk は `source_path#chunk_no` をキーにした埋め込み辞書。
    """
    initialize_hybrid_database()
    knowledge_paths = _list_knowledge_paths()
    source_paths = [path.name for path in knowledge_paths]

    created_docs = 0
    updated_docs = 0
    skipped_docs = 0
    inserted_chunks = 0

    with _connect_postgres() as connection:
        with connection.cursor() as cursor:
            _deactivate_removed_documents(cursor, source_paths)

            for path in knowledge_paths:
                source_path = path.name
                text = path.read_text(encoding="utf-8")
                source_hash = _compute_source_hash(text)
                title = _build_document_title(path, text)
                category = _build_document_category(path)
                chunks = build_document_chunks(text)

                existing = _fetch_existing_document(cursor, source_path)
                if existing and existing[1] == source_hash and not force_rebuild:
                    cursor.execute(
                        """
                        UPDATE documents
                        SET is_active = TRUE, updated_at = NOW()
                        WHERE id = %s
                        """,
                        (existing[0],),
                    )
                    skipped_docs += 1
                    continue

                if existing:
                    document_id = int(existing[0])
                    cursor.execute(
                        """
                        UPDATE documents
                        SET title = %s,
                            category = %s,
                            source_hash = %s,
                            is_active = TRUE,
                            updated_at = NOW()
                        WHERE id = %s
                        """,
                        (title, category, source_hash, document_id),
                    )
                    cursor.execute("DELETE FROM chunks WHERE document_id = %s", (document_id,))
                    updated_docs += 1
                else:
                    cursor.execute(
                        """
                        INSERT INTO documents (
                            source_path,
                            title,
                            category,
                            source_hash,
                            is_active,
                            updated_at
                        )
                        VALUES (%s, %s, %s, %s, TRUE, NOW())
                        RETURNING id
                        """,
                        (source_path, title, category, source_hash),
                    )
                    document_id = int(cursor.fetchone()[0])
                    created_docs += 1

                for chunk in chunks:
                    cursor.execute(
                        """
                        INSERT INTO chunks (
                            document_id,
                            chunk_no,
                            section,
                            body,
                            token_len,
                            search_terms,
                            updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, NOW())
                        RETURNING id
                        """,
                        (
                            document_id,
                            chunk.chunk_no,
                            chunk.section,
                            chunk.body,
                            len(chunk.body),
                            chunk.search_terms,
                        ),
                    )
                    chunk_id = int(cursor.fetchone()[0])
                    embedding_key = f"{source_path}#{chunk.chunk_no}"
                    vector = embeddings_by_chunk.get(embedding_key)
                    if vector is None:
                        continue

                    if len(vector) != HYBRID_EMBEDDING_DIM:
                        raise RuntimeError(
                            "埋め込み次元が設定値と一致しません。"
                            f" key={embedding_key}, got={len(vector)}, expected={HYBRID_EMBEDDING_DIM}"
                        )

                    cursor.execute(
                        """
                        INSERT INTO chunk_embeddings (
                            chunk_id,
                            model,
                            model_version,
                            dim,
                            embedding,
                            updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s::vector, NOW())
                        ON CONFLICT (chunk_id, model)
                        DO UPDATE
                        SET model_version = EXCLUDED.model_version,
                            dim = EXCLUDED.dim,
                            embedding = EXCLUDED.embedding,
                            updated_at = NOW()
                        """,
                        (
                            chunk_id,
                            model,
                            model,
                            HYBRID_EMBEDDING_DIM,
                            _to_vector_literal(vector),
                        ),
                    )
                    inserted_chunks += 1

    return {
        "created_documents": created_docs,
        "updated_documents": updated_docs,
        "skipped_documents": skipped_docs,
        "embedded_chunks": inserted_chunks,
    }


def search_keyword_candidates(terms: list[str], limit: int) -> list[ChunkCandidate]:
    """抽出語にもとづくキーワード検索候補を返す。"""
    if not terms or not has_hybrid_database_config():
        return []

    with _connect_postgres() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    c.id,
                    d.source_path,
                    c.section,
                    c.body,
                    (
                        SELECT COUNT(*)
                        FROM (
                            SELECT UNNEST(c.search_terms)
                            INTERSECT
                            SELECT UNNEST(%s::text[])
                        ) AS matched_terms
                    )::double precision AS score
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE d.is_active = TRUE
                  AND c.search_terms && %s::text[]
                ORDER BY score DESC, c.id ASC
                LIMIT %s
                """,
                (terms, terms, limit),
            )
            rows = cursor.fetchall()

    return [
        ChunkCandidate(
            chunk_id=int(row[0]),
            source_path=str(row[1]),
            section=str(row[2]),
            body=str(row[3]),
            score=float(row[4]),
            search_type="keyword",
        )
        for row in rows
    ]


def search_vector_candidates(
    embedding: list[float],
    *,
    limit: int,
    model: str = HYBRID_EMBEDDING_MODEL,
) -> list[ChunkCandidate]:
    """埋め込みベクトルの近傍検索候補を返す。"""
    if not embedding or not has_hybrid_database_config():
        return []
    if len(embedding) != HYBRID_EMBEDDING_DIM:
        raise RuntimeError(
            "検索クエリの埋め込み次元が設定値と一致しません。"
            f" got={len(embedding)}, expected={HYBRID_EMBEDDING_DIM}"
        )

    vector_literal = _to_vector_literal(embedding)
    with _connect_postgres() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT
                    c.id,
                    d.source_path,
                    c.section,
                    c.body,
                    (e.embedding::vector({HYBRID_EMBEDDING_DIM}) <=> %s::vector({HYBRID_EMBEDDING_DIM}))
                        AS distance
                FROM chunk_embeddings e
                JOIN chunks c ON c.id = e.chunk_id
                JOIN documents d ON d.id = c.document_id
                WHERE d.is_active = TRUE
                  AND e.model = %s
                ORDER BY e.embedding::vector({HYBRID_EMBEDDING_DIM}) <=> %s::vector({HYBRID_EMBEDDING_DIM})
                LIMIT %s
                """,
                (vector_literal, model, vector_literal, limit),
            )
            rows = cursor.fetchall()

    candidates = []
    for row in rows:
        distance = float(row[4])
        similarity = 1.0 - distance
        candidates.append(
            ChunkCandidate(
                chunk_id=int(row[0]),
                source_path=str(row[1]),
                section=str(row[2]),
                body=str(row[3]),
                score=similarity,
                search_type="vector",
            )
        )
    return candidates
