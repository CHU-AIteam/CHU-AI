"""埋め込みベクトル生成サービス。

Geminiの埋め込みAPI呼び出しを隠蔽し、
呼び出し側は文字列配列から数値ベクトル配列を得られるようにする。
"""

from __future__ import annotations

import os
from typing import Any

from chu_ai.config import HYBRID_EMBEDDING_DIM, HYBRID_EMBEDDING_MODEL
from chu_ai.services.generation_service import api_key_is_valid


def _resolve_api_key() -> str:
    """Gemini APIキーを取得し、最低限の妥当性を確認する。"""
    api_key = os.getenv("API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "API_KEY が設定されていません。hybridインデックス作成にはGemini APIキーが必要です。"
        )
    if not api_key_is_valid(api_key):
        raise RuntimeError(
            "API_KEY が不正です。Google AI Studioで発行された実際のGemini APIキーを設定してください。"
        )
    return api_key


def _extract_embedding_values(embedding_item: Any) -> list[float]:
    """Gemini埋め込み応答の1件から数値配列を取り出す。"""
    if embedding_item is None:
        return []

    if isinstance(embedding_item, dict):
        values = embedding_item.get("values")
        if isinstance(values, list):
            return [float(value) for value in values]

    values_attr = getattr(embedding_item, "values", None)
    if isinstance(values_attr, list):
        return [float(value) for value in values_attr]

    return []


def _extract_embeddings(response: Any) -> list[list[float]]:
    """Gemini応答から埋め込み配列一覧を取り出す。"""
    if response is None:
        return []

    if isinstance(response, dict):
        if isinstance(response.get("embeddings"), list):
            return [
                values
                for values in (
                    _extract_embedding_values(item) for item in response["embeddings"]
                )
                if values
            ]
        if response.get("embedding") is not None:
            values = _extract_embedding_values(response["embedding"])
            return [values] if values else []

    embeddings_attr = getattr(response, "embeddings", None)
    if isinstance(embeddings_attr, list):
        return [
            values
            for values in (_extract_embedding_values(item) for item in embeddings_attr)
            if values
        ]

    embedding_attr = getattr(response, "embedding", None)
    if embedding_attr is not None:
        values = _extract_embedding_values(embedding_attr)
        return [values] if values else []

    return []


def _build_embed_config(types_module, task_type: str):
    """Gemini SDKの設定オブジェクトを作る。"""
    config_class = getattr(types_module, "EmbedContentConfig", None)
    if config_class is None:
        return {
            "task_type": task_type,
            "output_dimensionality": HYBRID_EMBEDDING_DIM,
        }
    return config_class(
        task_type=task_type,
        output_dimensionality=HYBRID_EMBEDDING_DIM,
    )


def embed_texts(
    texts: list[str],
    *,
    model: str = HYBRID_EMBEDDING_MODEL,
    task_type: str = "RETRIEVAL_DOCUMENT",
) -> list[list[float]]:
    """複数テキストを埋め込みベクトルへ変換する。"""
    if not texts:
        return []

    try:
        from google import genai
        from google.genai import types
    except ImportError as error:
        raise RuntimeError(
            "Gemini SDKが見つかりません。`python3.11 -m pip install -r src/backend/requirements.txt` を実行してください。"
        ) from error

    client = genai.Client(api_key=_resolve_api_key())

    response = None
    config = _build_embed_config(types, task_type)
    try:
        response = client.models.embed_content(
            model=model,
            contents=texts,
            config=config,
        )
    except Exception:
        # SDKバージョン差異でconfigが受け取れない場合に備えて簡易呼び出しを試す。
        response = client.models.embed_content(
            model=model,
            contents=texts,
        )

    embeddings = _extract_embeddings(response)
    if len(embeddings) != len(texts):
        raise RuntimeError(
            "埋め込み件数が入力件数と一致しません。"
            f" texts={len(texts)}, embeddings={len(embeddings)}"
        )

    for index, vector in enumerate(embeddings):
        if len(vector) != HYBRID_EMBEDDING_DIM:
            raise RuntimeError(
                "埋め込み次元が設定値と一致しません。"
                f" index={index}, got={len(vector)}, expected={HYBRID_EMBEDDING_DIM}"
            )

    return embeddings


def embed_query_text(
    text: str,
    *,
    model: str = HYBRID_EMBEDDING_MODEL,
) -> list[float]:
    """検索クエリ用の埋め込みベクトルを生成する。"""
    vectors = embed_texts([text], model=model, task_type="RETRIEVAL_QUERY")
    return vectors[0]
