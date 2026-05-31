"""hybrid検索用インデックスを再構築するCLI。

knowledge Markdownをチャンク化してGemini埋め込みを作成し、
PostgreSQLのdocuments/chunks/chunk_embeddingsへ反映する。
"""

from __future__ import annotations

import argparse

from chu_ai.config import KNOWLEDGE_DIR, KNOWLEDGE_INDEX_FILE
from chu_ai.services.embedding_service import embed_texts
from chu_ai.services.hybrid_store_service import (
    build_document_chunks,
    has_hybrid_database_config,
    ingest_knowledge_to_hybrid_store,
    initialize_hybrid_database,
)


def _collect_chunk_payloads() -> list[tuple[str, str]]:
    """埋め込み対象チャンク一覧を `key, body` 形式で返す。"""
    payloads: list[tuple[str, str]] = []
    for path in sorted(KNOWLEDGE_DIR.glob("*.md")):
        if path.name == KNOWLEDGE_INDEX_FILE:
            continue

        text = path.read_text(encoding="utf-8")
        chunks = build_document_chunks(text)
        for chunk in chunks:
            key = f"{path.name}#{chunk.chunk_no}"
            payloads.append((key, chunk.body))
    return payloads


def _build_embeddings(payloads: list[tuple[str, str]], batch_size: int) -> dict[str, list[float]]:
    """チャンク本文を埋め込み化し、key->vector辞書を返す。"""
    embeddings: dict[str, list[float]] = {}
    total = len(payloads)
    if total == 0:
        return embeddings

    for start in range(0, total, batch_size):
        batch = payloads[start : start + batch_size]
        texts = [body for _, body in batch]
        vectors = embed_texts(texts)
        for (key, _), vector in zip(batch, vectors):
            embeddings[key] = vector
        print(f"Embedded chunks: {min(start + batch_size, total)}/{total}")

    return embeddings


def main() -> None:
    parser = argparse.ArgumentParser(description="hybrid検索インデックス再構築")
    parser.add_argument(
        "--force-rebuild",
        action="store_true",
        help="内容ハッシュが同じ文書も再取り込みする",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=16,
        help="埋め込みAPIへ一度に送るチャンク件数",
    )
    args = parser.parse_args()

    if not has_hybrid_database_config():
        raise RuntimeError(
            "POSTGRES_DSN が未設定です。src/.env にPostgreSQL DSNを設定してください。"
        )
    if args.batch_size <= 0:
        raise RuntimeError("--batch-size は1以上を指定してください。")

    print("Initializing hybrid database schema...")
    initialize_hybrid_database()

    print("Collecting knowledge chunks...")
    payloads = _collect_chunk_payloads()
    print(f"Total chunks to embed: {len(payloads)}")

    print("Building embeddings with Gemini...")
    embeddings = _build_embeddings(payloads, args.batch_size)

    print("Ingesting embeddings into PostgreSQL...")
    stats = ingest_knowledge_to_hybrid_store(
        embeddings,
        force_rebuild=args.force_rebuild,
    )

    print("Done.")
    print(f"  created_documents: {stats['created_documents']}")
    print(f"  updated_documents: {stats['updated_documents']}")
    print(f"  skipped_documents: {stats['skipped_documents']}")
    print(f"  embedded_chunks:   {stats['embedded_chunks']}")


if __name__ == "__main__":
    main()
