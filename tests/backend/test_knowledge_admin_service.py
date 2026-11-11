"""本番DBやAI APIを使わず、知識更新の公開・復元・失敗時の保持を検証。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main
import json
import sys
import threading
import time
import uuid
import zipfile


BACKEND = Path(__file__).resolve().parents[2] / "src/backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from chu_ai.services.knowledge_admin_service import KnowledgeConflict, KnowledgeManager


class FakeIndex:
    """indexerの公開トランザクション境界を再現し、失敗位置を制御する。"""

    def __init__(self):
        self.active = None
        self.documents = {}
        self.calls = []
        self.failpoint = None
        self.pause = False
        self.entered = threading.Event()
        self.release = threading.Event()
        self.mutex = threading.Lock()

    def read_active(self):
        with self.mutex:
            return self.active

    def index(self, directory, version_id, expected_version, progress):
        documents = {path.name: path.read_text(encoding="utf-8") for path in sorted(directory.glob("*.md"))}
        self.calls.append({"version_id": version_id, "expected_version": expected_version, "documents": documents})
        progress("embedding", "検索データを準備しています。", 50)
        self.entered.set()
        if self.pause and not self.release.wait(8):
            raise RuntimeError("Test indexer was not released")
        with self.mutex:
            if self.active != expected_version:
                raise KnowledgeConflict("別の処理が先に知識を更新しました。")
            if self.failpoint == "before_commit":
                raise RuntimeError("private-api-key-not-for-users")
            # Public data is changed only here, after every preparation succeeds.
            self.active = version_id
            self.documents = documents
            if self.failpoint == "after_commit":
                raise RuntimeError("Connection lost after committed transaction")
        return {"created_documents": len(documents), "updated_documents": 0, "embedded_chunks": len(documents)}


class KnowledgeAdminTests(TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="como-knowledge-admin-")
        self.root = Path(self.temporary.name) / "runtime"
        self.seed = Path(self.temporary.name) / "seed"
        self.seed.mkdir()
        self.seed_contents = {
            "01_facilities.md": "# コモンズ案内\n\n## 場所\n不言実行館の1階です。\n",
            "02_opening_hours.md": "# 開館時間\n\n## 平日\n9時から17時までです。\n",
        }
        for name, content in self.seed_contents.items():
            (self.seed / name).write_text(content, encoding="utf-8")
        (self.seed / "99_knowledge_h2_summary_index.md").write_text("# 目次\n本文ではありません。", encoding="utf-8")
        self.index = FakeIndex()
        self.manager = self.new_manager()
        self.started = []

    def tearDown(self):
        self.index.release.set()
        for manager, job_id in self.started:
            self.wait(manager, job_id)
        self.temporary.cleanup()

    def new_manager(self):
        return KnowledgeManager(root=self.root, seed=self.seed, read_active=self.index.read_active, indexer=self.index.index)

    def wait(self, manager, job_id):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            job = manager.job(job_id)
            if job["status"] in {"succeeded", "failed"} and not manager._busy_job():
                return job
            time.sleep(0.01)
        self.fail(f"知識更新jobが完了しませんでした: {job_id}")

    def track(self, job_id, manager=None):
        manager = manager or self.manager
        self.started.append((manager, job_id))
        return self.wait(manager, job_id)

    def initialize(self):
        job = self.track(self.manager.initialize())
        self.assertEqual(job["status"], "succeeded", job)
        return job["result"]["version_id"]

    def publish(self, preview, manager=None):
        manager = manager or self.manager
        job = self.track(manager.publish(preview["preview_id"], preview["base_version"]), manager)
        self.assertEqual(job["status"], "succeeded", job)
        return job["result"]["version_id"]

    def test_initialization_copies_seed_excludes_summary_and_creates_history(self):
        before = self.manager.status()
        self.assertFalse(before["initialized"])
        self.assertTrue(before["initialization_required"])
        self.assertEqual(len(before["documents"]), 2)
        self.assertEqual(before["history"], [])
        self.assertEqual(self.manager.document("01_facilities.md")["title"], "コモンズ案内")
        version = self.initialize()
        after = self.manager.status()
        self.assertTrue(after["initialized"])
        self.assertFalse(after["initialization_required"])
        self.assertEqual(after["active_version"], version)
        self.assertIsNotNone(after["last_updated"])
        self.assertEqual(set(self.index.documents), set(self.seed_contents))
        self.assertEqual(self.index.documents, self.seed_contents)
        self.assertEqual(len(after["history"]), 1)
        self.assertTrue(after["history"][0]["current"])
        self.assertEqual(after["history"][0]["document_count"], 2)

    def test_initialize_again_is_idempotent_and_does_not_reindex(self):
        version = self.initialize()
        calls = len(self.index.calls)
        job = self.track(self.manager.initialize())
        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(self.manager.status()["active_version"], version)
        self.assertEqual(len(self.index.calls), calls)
        self.assertEqual(len(self.manager.status()["history"]), 1)

    def test_upload_preview_add_publish_and_original_are_durable(self):
        first = self.initialize()
        original = "学生証を持って受付へ来てください。".encode("utf-8")
        preview = self.manager.preview(filename="E:\\更新用\\備品貸出.txt", data=original)
        self.assertEqual(preview["action"], "add")
        self.assertEqual(preview["base_version"], first)
        self.assertEqual(preview["source_filename"], "備品貸出.txt")
        self.assertEqual(preview["previous_content"], "")
        self.assertEqual(self.index.read_active(), first, "preview must not publish data")
        self.assertEqual(len(self.index.documents), 2)
        second = self.publish(preview)
        self.assertNotEqual(second, first)
        document = self.manager.document(preview["target_id"])
        self.assertEqual(document["title"], "備品貸出")
        self.assertEqual(document["content"].strip(), "# 備品貸出\n\n" + original.decode())
        self.assertEqual(document["origin"], "USB・ファイル")
        self.assertEqual((self.root / "originals" / preview["original"]).read_bytes(), original)
        self.assertEqual(len(self.index.documents), 3)
        self.assertEqual(self.index.calls[-1]["expected_version"], first)
        self.assertEqual((self.seed / "01_facilities.md").read_text(encoding="utf-8"), self.seed_contents["01_facilities.md"])

    def test_explicit_replacement_keeps_document_id_and_old_preview_content(self):
        self.initialize()
        target = "02_opening_hours.md"
        preview = self.manager.preview(filename="新しい時間.txt", data="平日は18時までです。".encode(), target_id=target)
        self.assertEqual(preview["action"], "replace")
        self.assertEqual(preview["target_id"], target)
        self.assertIn("17時", preview["previous_content"])
        self.publish(preview)
        self.assertEqual(len(self.manager.status()["documents"]), 2)
        self.assertIn("18時", self.manager.document(target)["content"])
        self.assertIn("17時", (self.seed / target).read_text(encoding="utf-8"))

    def test_same_source_stem_suggests_replacement_and_manual_edit_works(self):
        self.initialize()
        added = self.manager.preview(filename="貸出案内.txt", data="初めの内容".encode())
        self.publish(added)
        replacement = self.manager.preview(filename="貸出案内.md", data="# 貸出案内\n\n更新した内容".encode())
        self.assertEqual(replacement["action"], "replace")
        self.assertEqual(replacement["target_id"], added["target_id"])
        self.assertTrue(any("同じ名前" in warning for warning in replacement["warnings"]))
        self.publish(replacement)
        edited = self.manager.preview(title="貸出案内 改定版", content="# 貸出案内 改定版\n\n画面から変更しました。", target_id=added["target_id"])
        self.assertEqual(edited["origin"], "画面で編集")
        self.publish(edited)
        current = self.manager.document(added["target_id"])
        self.assertEqual(current["title"], "貸出案内 改定版")
        self.assertIn("画面から変更", current["content"])
        self.assertEqual(len(self.manager.status()["documents"]), 3)

    def test_word_preview_reconfirmation_preserves_original_and_source_filename(self):
        self.initialize()
        archive = BytesIO()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as word:
            word.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
            word.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t>受付案内</w:t></w:r></w:p><w:p><w:r><w:t>受付は1階です。</w:t></w:r></w:p></w:body></w:document>')
        original = archive.getvalue()
        uploaded = self.manager.preview(filename="受付案内.docx", data=original)
        reviewed = self.manager.preview(title="受付案内 確認済み", content="# 受付案内\n\n受付は1階です。学生証をご持参ください。", source_preview_id=uploaded["preview_id"])
        self.assertEqual(reviewed["source_filename"], "受付案内.docx")
        self.assertEqual(reviewed["original"], uploaded["original"])
        self.assertEqual(reviewed["target_id"], uploaded["target_id"])
        self.assertEqual(reviewed["origin"], "USB・ファイル")
        self.assertEqual(reviewed["warnings"], uploaded["warnings"])
        self.publish(reviewed)
        document = self.manager.document(reviewed["target_id"])
        self.assertEqual(document["source_filename"], "受付案内.docx")
        self.assertEqual(document["original"], uploaded["original"])
        self.assertIn("学生証", document["content"])
        self.assertEqual((self.root / "originals" / uploaded["original"]).read_bytes(), original)
        with self.assertRaises(KnowledgeConflict):
            self.manager.preview(title="古い確認", content="本文", source_preview_id=uploaded["preview_id"])

    def test_reconfirmation_of_replacement_keeps_target_and_rejects_expired_source(self):
        self.initialize()
        source = self.manager.preview(filename="新時間.txt", data="平日は18時までです。".encode(), target_id="02_opening_hours.md")
        reviewed = self.manager.preview(title="開館時間 改定版", content="# 開館時間\n\n平日は18時までです。", source_preview_id=source["preview_id"])
        self.assertEqual(reviewed["action"], "replace")
        self.assertEqual(reviewed["target_id"], "02_opening_hours.md")
        self.assertEqual(reviewed["source_filename"], "新時間.txt")
        self.assertEqual(reviewed["original"], source["original"])
        self.assertIn("17時", reviewed["previous_content"])
        path = self.root / "previews" / (source["preview_id"] + ".json")
        expired = json.loads(path.read_text(encoding="utf-8"))
        expired["created_at"] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        path.write_text(json.dumps(expired, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(KnowledgeConflict, "有効期限"):
            self.manager.preview(title="期限", content="本文", source_preview_id=source["preview_id"])
        self.publish(reviewed)
        self.assertEqual(len(self.manager.status()["documents"]), 2)
        self.assertEqual(self.manager.document("02_opening_hours.md")["original"], source["original"])

    def test_restore_creates_a_new_version_and_keeps_full_history(self):
        first = self.initialize()
        added = self.manager.preview(filename="新しい知識.txt", data="追加した情報".encode())
        second = self.publish(added)
        edited = self.manager.preview(title="案内", content="変更後の施設案内", target_id="01_facilities.md")
        third = self.publish(edited)
        restored_job = self.track(self.manager.restore(first, third))
        self.assertEqual(restored_job["status"], "succeeded", restored_job)
        fourth = restored_job["result"]["version_id"]
        self.assertNotIn(fourth, [first, second, third])
        self.assertEqual(self.index.documents, self.seed_contents)
        history = self.manager.status()["history"]
        self.assertEqual([item["id"] for item in history], [fourth, third, second, first])
        self.assertEqual([item["current"] for item in history], [True, False, False, False])
        self.assertEqual(len(self.manager.status()["documents"]), 2)
        self.assertIn("復元", history[0]["label"])

    def test_restarting_manager_and_changing_git_seed_does_not_replace_runtime_data(self):
        self.initialize()
        edited = self.manager.preview(title="開館時間", content="# 開館時間\n\n18時まで。", target_id="02_opening_hours.md")
        version = self.publish(edited)
        (self.seed / "02_opening_hours.md").write_text("# 開館時間\nGitで配布された別の初期知識。", encoding="utf-8")
        restarted = self.new_manager()
        self.assertEqual(restarted.status()["active_version"], version)
        self.assertEqual(len(restarted.status()["history"]), 2)
        self.assertEqual(restarted.document("02_opening_hours.md")["content"].strip(), "# 開館時間\n\n18時まで。")

    def test_stale_preview_and_wrong_base_are_rejected_without_indexing(self):
        first = self.initialize()
        a = self.manager.preview(title="A", content="追加A")
        b = self.manager.preview(title="B", content="追加B")
        second = self.publish(a)
        calls = len(self.index.calls)
        with self.assertRaises(KnowledgeConflict):
            self.manager.publish(b["preview_id"], first)
        with self.assertRaises(KnowledgeConflict):
            self.manager.publish(b["preview_id"], second)
        with self.assertRaises(KnowledgeConflict):
            self.manager.restore(first, first)
        self.assertEqual(self.index.read_active(), second)
        self.assertEqual(len(self.index.calls), calls)

    def test_file_lock_prevents_parallel_updates_from_different_managers(self):
        first = self.initialize()
        a = self.manager.preview(title="A", content="追加A")
        b = self.manager.preview(title="B", content="追加B")
        other = self.new_manager()
        self.index.pause = True
        self.index.entered.clear()
        job_id = self.manager.publish(a["preview_id"], first)
        self.started.append((self.manager, job_id))
        self.assertTrue(self.index.entered.wait(3), "indexer starts asynchronously")
        self.assertTrue(other.status()["busy"])
        with self.assertRaises(KnowledgeConflict):
            other.publish(b["preview_id"], first)
        with self.assertRaises(KnowledgeConflict):
            other.restore(first, first)
        self.assertEqual(self.index.read_active(), first)
        self.index.release.set()
        completed = self.wait(self.manager, job_id)
        self.assertEqual(completed["status"], "succeeded", completed)
        self.assertFalse(other.status()["busy"])

    def test_failure_before_commit_keeps_old_knowledge_and_can_retry(self):
        first = self.initialize()
        before = dict(self.index.documents)
        preview = self.manager.preview(title="失敗後に再試行", content="新しい本文")
        self.index.failpoint = "before_commit"
        failed = self.track(self.manager.publish(preview["preview_id"], first))
        self.assertEqual(failed["status"], "failed")
        self.assertNotIn("private-api-key", failed["message"])
        self.assertEqual(self.index.read_active(), first)
        self.assertEqual(self.index.documents, before)
        self.assertEqual(len(self.manager.status()["history"]), 1)
        self.assertEqual(len(self.manager.status()["documents"]), 2)
        self.index.failpoint = None
        published = self.publish(preview)
        self.assertNotEqual(published, first)
        self.assertEqual(len(self.manager.status()["documents"]), 3)

    def test_exception_after_db_commit_is_recovered_as_success(self):
        first = self.initialize()
        preview = self.manager.preview(title="コミット済みの知識", content="反映済みの本文")
        self.index.failpoint = "after_commit"
        job = self.track(self.manager.publish(preview["preview_id"], first))
        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(job["result"]["version_id"], self.index.read_active())
        self.assertEqual(self.manager.document(preview["target_id"])["content"].strip(), "# コミット済みの知識\n\n反映済みの本文")
        self.assertTrue(self.new_manager().status()["initialized"])

    def test_interrupted_job_reconciles_with_active_db_version(self):
        version = self.initialize()
        for committed in [True, False]:
            job_id = uuid.uuid4().hex
            target = version if committed else uuid.uuid4().hex
            path = self.root / "jobs" / (job_id + ".json")
            path.write_text(json.dumps({"id": job_id, "status": "running", "target_version": target, "progress": 90, "stage": "publishing"}), encoding="utf-8")
            job = self.new_manager().job(job_id)
            self.assertEqual(job["status"], "succeeded" if committed else "failed")
            self.assertEqual(self.index.read_active(), version)
            persisted = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["status"], job["status"])

    def test_invalid_paths_missing_records_and_empty_content_are_rejected(self):
        with self.assertRaises(KnowledgeConflict):
            self.manager.preview(title="初期化前", content="本文")
        self.initialize()
        for value in ["../outside.md", "/etc/passwd.md", "folder/file.md", "folder\\file.md", ".hidden.md", "99_knowledge_h2_summary_index.md", "missing.md"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.manager.document(value)
        for value in ["", "../outside", "a" * 31, "A" * 32, "x" * 32]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.manager.job(value)
                with self.assertRaises(ValueError):
                    self.manager.publish(value, self.index.read_active())
                with self.assertRaises(ValueError):
                    self.manager.restore(value, self.index.read_active())
        with self.assertRaises(ValueError):
            self.manager.job(uuid.uuid4().hex)
        with self.assertRaises(ValueError):
            self.manager.restore(uuid.uuid4().hex, self.index.read_active())
        for content in ["", " \n\t ", "bad\x00text", "あ" * 200001]:
            with self.subTest(length=len(content)), self.assertRaises(ValueError):
                self.manager.preview(title="不正本文", content=content)
        for title in ["改\n行", "改\r行", "あ" * 151]:
            with self.subTest(title=title[:5]), self.assertRaises(ValueError):
                self.manager.preview(title=title, content="本文")
        with self.assertRaises(ValueError):
            self.manager.preview(title="不明", content="本文", target_id="../outside.md")

    def test_preview_expires_after_one_day(self):
        version = self.initialize()
        preview = self.manager.preview(title="期限", content="本文")
        path = self.root / "previews" / (preview["preview_id"] + ".json")
        saved = json.loads(path.read_text(encoding="utf-8"))
        saved["created_at"] = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        path.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(KnowledgeConflict, "有効期限"):
            self.manager.publish(preview["preview_id"], version)
        self.assertEqual(self.index.read_active(), version)

    def test_empty_seed_fails_without_publishing(self):
        for path in self.seed.glob("*.md"):
            path.unlink()
        job = self.track(self.manager.initialize())
        self.assertEqual(job["status"], "failed", job)
        self.assertIn("初期知識", job["message"])
        self.assertIsNone(self.index.read_active())
        self.assertEqual(self.index.calls, [])


if __name__ == "__main__":
    main()
