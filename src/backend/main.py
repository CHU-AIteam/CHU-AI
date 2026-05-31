"""ASGIサーバ起動用の最小エントリポイント。

`chu_ai.api` の `app` を公開することだけに責務を限定し、
API実装や設定ロジックは持たない。
"""

from chu_ai.api import app

__all__ = ["app"]
