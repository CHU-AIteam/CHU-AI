"""隔離したpgvector DBで知識公開と検索のトランザクションを検証する。

COMO_TEST_POSTGRES_DSN を明示したときだけ実行する。
接続先は loopback の chuai_ops_test 専用DBに限定する。
テストごとにこのDBの知識テーブルだけを空にするため、運用DBを指定しない。
Gemini等の外部APIは呼ばず、埋め込み生成だけを固定ベクトルへ置き換える。
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main, mock, skipUnless
import os
import sys
import threading
import time
import uuid


BACKEND = Path(__file__).resolve().parents[2] / "src/backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from chu_ai.services import hybrid_store_service as store
from chu_ai.services.knowledge_admin_service import KnowledgeManager, index_version


TEST_DSN = os.environ.get("COMO_TEST_POSTGRES_DSN", "").strip()
DIMENSION = 768
QUERY_VECTOR = [1.0] + [0.0] * (DIMENSION - 1)


@skipUnless(TEST_DSN, "COMO_TEST_POSTGRES_DSN 未設定のため実DB検証を省略")
class KnowledgePostgresIntegrationTests(TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg.conninfo import conninfo_to_dict

        settings = conninfo_to_dict(TEST_DSN)
        if settings.get("dbname") != "chuai_ops_test" or settings.get("host") not in {"127.0.0.1", "localhost", "::1"}:
            raise RuntimeError("integrationテストはloopbackの専用DB chuai_ops_testだけで実行できます。")
        if settings.get("hostaddr") and settings["hostaddr"] not in {"127.0.0.1", "::1"}:
            raise RuntimeError("integrationテストのhostaddrはloopbackに限定してください。")
        with psycopg.connect(TEST_DSN, connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_database()")
                if cursor.fetchone()[0] != "chuai_ops_test":
                    raise RuntimeError("指定された専用テストDBに接続できませんでした。")
        if store.HYBRID_EMBEDDING_DIM != DIMENSION:
            raise RuntimeError("このintegrationテストは768次元の設定で実行してください。")
        with mock.patch.object(store, "POSTGRES_DSN", TEST_DSN):
            store.initialize_hybrid_database()

    def setUp(self):
        self.patch_dsn = mock.patch.object(store, "POSTGRES_DSN", TEST_DSN)
        self.patch_dsn.start()
        self.addCleanup(self.patch_dsn.stop)
        # No live AI credentials may be used even if the test process inherited them.
        self.patch_credentials = mock.patch.dict(os.environ, {"API_KEY": "", "GEMINI_API_KEY": ""})
        self.patch_credentials.start()
        self.addCleanup(self.patch_credentials.stop)
        self.embed = mock.patch("chu_ai.services.embedding_service.embed_texts", side_effect=self.vectors)
        self.embedding_mock = self.embed.start()
        self.addCleanup(self.embed.stop)
        with store._connect_postgres() as connection:
            with connection.cursor() as cursor:
                cursor.execute("TRUNCATE TABLE chunk_embeddings, chunks, documents, como_knowledge_state RESTART IDENTITY CASCADE")
        self.temporary = TemporaryDirectory(prefix="como-pg-integration-")
        self.addCleanup(self.temporary.cleanup)
        self.seed = Path(self.temporary.name) / "seed"
        self.seed.mkdir()
        self.runtime = Path(self.temporary.name) / "runtime"
        self.seed_texts = {
            "01_facilities.md": "# コモンズ案内\n\n## 場所\n不言実行館の1階です。 OLDPLACE\n",
            "02_opening_hours.md": "# 開館時間\n\n## 平日\n受付は17時までです。 OLDHOURS\n",
        }
        for name, content in self.seed_texts.items():
            (self.seed / name).write_text(content, encoding="utf-8")
        (self.seed / "99_knowledge_h2_summary_index.md").write_text("# 検索用目次のみ", encoding="utf-8")
        self.manager = self.new_manager()
        self.jobs = []

    def tearDown(self):
        for job_id in self.jobs:
            self.wait_job(job_id)

    @staticmethod
    def vectors(texts, **kwargs):
        return [list(QUERY_VECTOR) for _ in texts]

    def new_manager(self):
        return KnowledgeManager(root=self.runtime, seed=self.seed,
                                read_active=store.get_active_knowledge_version, indexer=index_version)

    def wait_job(self, job_id):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            result = self.manager.job(job_id)
            if result["status"] in {"succeeded", "failed"} and not self.manager._busy_job():
                return result
            time.sleep(0.01)
        self.fail("実DBでの知識更新が10秒以内に完了しませんでした。")

    def finish(self, job_id):
        self.jobs.append(job_id)
        result = self.wait_job(job_id)
        self.assertEqual(result["status"], "succeeded", result)
        self.assertEqual(result["result"]["version_id"], store.get_active_knowledge_version())
        return result["result"]["version_id"]

    def initialize(self):
        return self.finish(self.manager.initialize())

    def publish(self, preview):
        return self.finish(self.manager.publish(preview["preview_id"], preview["base_version"]))

    def keyword(self, marker):
        return store.search_keyword_candidates([marker.lower()], 100)

    def vector(self):
        return store.search_vector_candidates(QUERY_VECTOR, limit=100)

    def db_snapshot(self):
        with store._connect_postgres() as connection:
            with connection.cursor() as cursor:
                tables = {}
                for table, columns, order in [
                    ("como_knowledge_state", "singleton, version_id, updated_at", "singleton"),
                    ("documents", "id, source_path, title, category, source_hash, is_active, updated_at", "id"),
                    ("chunks", "id, document_id, chunk_no, section, body, token_len, search_terms, updated_at", "id"),
                    ("chunk_embeddings", "chunk_id, model, model_version, dim, embedding::text, updated_at", "chunk_id, model"),
                ]:
                    # All identifiers above are fixed test constants.
                    cursor.execute(f"SELECT {columns} FROM {table} ORDER BY {order}")
                    tables[table] = cursor.fetchall()
                return tables

    def prepared_replacement(self):
        prepared = Path(self.temporary.name) / ("prepared-" + uuid.uuid4().hex)
        prepared.mkdir()
        (prepared / "01_facilities.md").write_text("# コモンズ案内\n\n## 場所\n変更した場所です。 NEWPLACE\n", encoding="utf-8")
        (prepared / "02_opening_hours.md").write_text("# 開館時間\n\n## 平日\n受付は18時までです。 NEWHOURS\n", encoding="utf-8")
        vectors = {}
        for path in sorted(prepared.glob("*.md")):
            for chunk in store.build_document_chunks(path.read_text(encoding="utf-8")):
                vectors[f"{path.name}#{chunk.chunk_no}"] = list(QUERY_VECTOR)
        return prepared, vectors

    def test_initialize_add_replace_and_restore_update_real_search_and_marker(self):
        first = self.initialize()
        self.assertTrue(self.keyword("OLDHOURS"))
        self.assertTrue(self.keyword("OLDPLACE"))
        self.assertEqual(set(store.get_indexed_document_hashes()), set(self.seed_texts))
        self.assertEqual({item.source_path for item in self.vector()}, set(self.seed_texts))
        added = self.manager.preview(filename="貸出案内.txt", data="学生証を持参してください。 BORROWING".encode())
        second = self.publish(added)
        self.assertNotEqual(first, second)
        self.assertEqual({item.source_path for item in self.keyword("BORROWING")}, {added["target_id"]})
        self.assertIn(added["target_id"], {item.source_path for item in self.vector()})
        changed = self.manager.preview(title="開館時間", content="# 開館時間\n\n## 平日\n受付は18時までです。 NEWHOURS", target_id="02_opening_hours.md")
        third = self.publish(changed)
        self.assertNotEqual(second, third)
        self.assertFalse(self.keyword("OLDHOURS"))
        self.assertTrue(self.keyword("NEWHOURS"))
        self.assertTrue(any("NEWHOURS" in item.body for item in self.vector()))
        fourth = self.finish(self.manager.restore(first, third))
        self.assertNotEqual(fourth, first)
        self.assertTrue(self.keyword("OLDHOURS"))
        self.assertFalse(self.keyword("NEWHOURS"))
        self.assertFalse(self.keyword("BORROWING"))
        self.assertEqual({item.source_path for item in self.vector()}, set(self.seed_texts))
        self.assertEqual(len(self.manager.status()["history"]), 4)

    def test_partial_sql_updates_rollback_for_missing_wrong_dimension_and_nonfinite_vectors(self):
        first = self.initialize()
        before = self.db_snapshot()
        for invalid in ["missing", "dimension", "nonfinite"]:
            with self.subTest(invalid=invalid):
                directory, vectors = self.prepared_replacement()
                # The second file's second chunk fails only after earlier document/chunk SQL ran.
                target = "02_opening_hours.md#2"
                self.assertIn(target, vectors)
                if invalid == "missing":
                    del vectors[target]
                elif invalid == "dimension":
                    vectors[target] = [1.0]
                else:
                    vectors[target] = [float("nan")] + [0.0] * (DIMENSION - 1)
                with self.assertRaises(RuntimeError):
                    store.ingest_knowledge_to_hybrid_store(vectors, knowledge_dir=directory,
                        version_id=uuid.uuid4().hex, expected_version=first, require_all_embeddings=True)
                self.assertEqual(self.db_snapshot(), before, "all content and the active marker must roll back together")
                self.assertTrue(self.keyword("OLDHOURS"))
                self.assertFalse(self.keyword("NEWHOURS"))
                self.assertFalse(self.keyword("NEWPLACE"))

    def test_stale_expected_version_cannot_change_database(self):
        first = self.initialize()
        changed = self.manager.preview(title="改定", content="改定後の情報。 PUBLISHED")
        second = self.publish(changed)
        before = self.db_snapshot()
        directory, vectors = self.prepared_replacement()
        with self.assertRaisesRegex(RuntimeError, "別の操作"):
            store.ingest_knowledge_to_hybrid_store(vectors, knowledge_dir=directory,
                version_id=uuid.uuid4().hex, expected_version=first, require_all_embeddings=True)
        self.assertEqual(store.get_active_knowledge_version(), second)
        self.assertEqual(self.db_snapshot(), before)
        self.assertTrue(self.keyword("PUBLISHED"))

    def test_restart_and_git_seed_changes_keep_usb_knowledge_in_database(self):
        self.initialize()
        uploaded = self.manager.preview(filename="USB案内.md", data="# USB案内\n\n本番PCで更新した内容。 USBSAVED".encode())
        active = self.publish(uploaded)
        for path in self.seed.glob("*.md"):
            path.write_text("# Gitから届いた初期情報\nDIFFERENTSEED", encoding="utf-8")
        restarted = self.new_manager()
        self.assertEqual(restarted.status()["active_version"], active)
        self.assertIn("USBSAVED", restarted.document(uploaded["target_id"])["content"])
        self.assertTrue(self.keyword("USBSAVED"))
        self.assertFalse(self.keyword("DIFFERENTSEED"))
        calls = self.embedding_mock.call_count
        result = self.wait_job(restarted.initialize())
        self.assertEqual(result["status"], "succeeded", result)
        self.assertEqual(store.get_active_knowledge_version(), active)
        self.assertEqual(self.embedding_mock.call_count, calls, "an already initialized PC must not re-import Git seed")

    def test_manager_reports_failure_and_keeps_existing_search_when_vectors_are_invalid(self):
        first = self.initialize()
        before = self.db_snapshot()
        preview = self.manager.preview(title="開館時間", content="# 開館時間\n\n## 平日\n更新した本文。 BROKENVECTOR", target_id="02_opening_hours.md")
        self.embedding_mock.side_effect = lambda texts, **kwargs: [[1.0] for _ in texts]
        job_id = self.manager.publish(preview["preview_id"], first)
        self.jobs.append(job_id)
        result = self.wait_job(job_id)
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(self.db_snapshot(), before)
        self.assertEqual(self.manager.status()["active_version"], first)
        self.assertTrue(self.keyword("OLDHOURS"))
        self.assertFalse(self.keyword("BROKENVECTOR"))

    def test_keyword_and_vector_queries_share_snapshot_across_concurrent_publish(self):
        first = self.initialize()
        preview = self.manager.preview(title="開館時間", content="# 開館時間\n\n## 平日\n18時までです。 NEWHOURS", target_id="02_opening_hours.md")
        start = threading.Event()
        published = threading.Event()
        outcome = {}
        original_keyword = store.search_keyword_candidates

        def publisher():
            try:
                if not start.wait(8):
                    raise RuntimeError("keyword query did not start")
                job_id = self.manager.publish(preview["preview_id"], first)
                self.jobs.append(job_id)
                outcome["job"] = self.wait_job(job_id)
            except Exception as error:
                outcome["error"] = error
            finally:
                published.set()

        def keyword_then_publish(*args, **kwargs):
            candidates = original_keyword(*args, **kwargs)
            outcome["snapshot_connection"] = kwargs.get("connection") is not None
            start.set()
            if not published.wait(8):
                raise RuntimeError("concurrent publish did not commit")
            if "error" in outcome:
                raise outcome["error"]
            return candidates

        worker = threading.Thread(target=publisher, name="test-concurrent-publish")
        worker.start()
        try:
            with mock.patch.object(store, "search_keyword_candidates", side_effect=keyword_then_publish):
                keyword, vector = store.search_hybrid_candidates(["oldhours"], QUERY_VECTOR,
                    keyword_limit=100, vector_limit=100)
        finally:
            start.set()
            worker.join(10)
        self.assertFalse(worker.is_alive(), "test publisher finishes")
        self.assertTrue(outcome["snapshot_connection"])
        self.assertEqual(outcome["job"]["status"], "succeeded", outcome)
        self.assertNotEqual(store.get_active_knowledge_version(), first)
        old_ids = {candidate.chunk_id for candidate in keyword if "OLDHOURS" in candidate.body}
        self.assertTrue(old_ids, "keyword read sees old content")
        self.assertTrue(old_ids.issubset({candidate.chunk_id for candidate in vector}), "vector read retains the same old MVCC snapshot")
        self.assertFalse(any("NEWHOURS" in candidate.body for candidate in vector))
        self.assertTrue(self.keyword("NEWHOURS"), "a new transaction sees the published knowledge")
        self.assertFalse(self.keyword("OLDHOURS"))


if __name__ == "__main__":
    main()
