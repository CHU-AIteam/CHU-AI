"""キーワード検索向けの検索語抽出サービス。

質問文を軽く正規化し、ノイズ語を除いた検索語リストを返す。
文書検索の前処理だけを担当し、検索実行は担当しない。
"""

from __future__ import annotations

import re


SPLITTER_WORDS = [
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
"""自然文から検索語を切り出すための分割語"""

STOP_WORDS = {
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
"""検索語として弱い語の除外リスト"""


def extract_search_terms(text: str) -> list[str]:
    """質問文から知識選定用の検索語を抽出する関数。"""
    normalized = text.lower()
    for word in SPLITTER_WORDS:
        normalized = normalized.replace(word, " ")

    terms = re.findall(r"[0-9A-Za-zぁ-んァ-ヶ一-龯ー]{2,}", normalized)
    return [term for term in terms if term not in STOP_WORDS]
