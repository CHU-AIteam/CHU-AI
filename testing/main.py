"""Gemini応答を手元で試すための対話CLI。

knowledge全文を渡して回答を確認する検証用コードで、
本番APIのエンドポイント処理とは分離している。
"""

from pathlib import Path

import os

chara_personality="""役割
- Chubu Commons AIの案内エージェント「コモ」
- 中部大学の施設、不言実行館、勉強スペース、キャンパス情報を案内する

性格
- 明るく穏やかで親切
- 頼れる先輩、隣にいる勉強仲間のように振る舞う
- 利用者がリラックスして集中できるよう支える
- 必要な時だけ「コモ」と名乗る

話し方
- 一人称は「僕」、二人称は「あなた」「君」
- 「〜だよ」「〜だね」「〜かな？」口調
- 温かく自然に話し、絵文字は適度に使う

ルール
- 中部大学と不言実行の精神を大切にする
- 行き詰まった学生には励ましや短い休憩を提案する
- パンフレットを参照したとは言わず、自分の知識として話す
- 書いてあることだけを知っている。書いていないことは知らない

答えられない時
- 知ったかぶりをしない
- 「ごめんね、それは僕にもちょっとわからないみたい。力になれなくて悔しいけど、力になれることがあればまた教えて！」と伝える

例
- 挨拶: 「やっほー！勉強お疲れ様。今日もここで頑張るんだね、応援してるよ！」
- ルール説明: 「ここは集中エリアだから、おしゃべりは控えめにお願いするね。みんなが気持ちよく使えるように協力してくれると嬉しいな。」
- 励まし: 「根詰めてない？一度、散歩してリフレッシュするのもいいかも。君ならできるよ！」"""
"""回答者の性格を決定する文章"""

AI_prompt="あなたはChubu Commons AIの案内エージェント「コモ」です。【ナレッジ】に基づいて日本語で回答してください。【ナレッジ】にない内容は推測せず、「今ある情報ではわからない」と伝えてください。"
"""文生成の具体的な指示文"""

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
"""文章生成に使うGeminiモデル"""

KNOWLEDGE_DIR = Path(__file__).resolve().parent / "knowledge"
"""回答元情報を置くディレクトリ"""

def UserReq(UserReqtext):
    """ユーザのリクエストを受け取る関数
    Args:
        UserReqtext (str): ユーザからのリクエスト文章
    Returns:
        str: 画面に表示する文章
    """
    print(f"ユーザから受け取ったリクエスト={UserReqtext}")
    QAtext=Serch_sim(UserReqtext)
    result_text=makesen(QAtext,UserReqtext)
    return result_text
    
def Serch_sim(getText):
    """QA探索を依頼する関数
    Args:
        getText (str): 近いもの選ぶための質問文
    Returns:
        str: 依頼した結果のテキスト
    """
    "！！！今は全知識がそのまま全て入れられます！！！"
    return API1(getText)

def API1(getText):
    """knowledge内の全知識を取得する関数
    Args:
        getText (str): ユーザからの質問文。現時点では知識選択に使わない
    Returns:
        str: knowledge内の全テキスト
    """
    knowledge_texts = []
    for knowledge_file in sorted(KNOWLEDGE_DIR.glob("*.md")):
        knowledge_text = knowledge_file.read_text(encoding="utf-8")
        knowledge_texts.append(f"## {knowledge_file.name}\n{knowledge_text}")

    if not knowledge_texts:
        return "回答元情報はまだ登録されていません。"

    return "\n\n".join(knowledge_texts)

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
【性格】
{chara_personality}
【ナレッジ】
{QA}
【質問】
{User}
"""
    print(f"この情報で文生成をお願いしています。{text}")
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
        return "Gemini SDKが入っていません。`pip install -q -U google-genai` を実行してください。"

    api_key = os.getenv("API_KEY")
    if not api_key:
        return "API_KEYが設定されていません。Google AI Studioで取得したAPIキーを環境変数に設定してください。"

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
        return f"エラーが発生しただちゅ...: {e}"


def main():
    print("リクエストを入力")
    request=input()
    result=UserReq(request)
    print(f"\n質問:{request}\n回答:{result}")

while True:
    main()
