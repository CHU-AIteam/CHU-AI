"""管理APIの認証、ローカル制限、入力検証、エラー境界を検証する。"""

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import TestCase, main, mock
import base64
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient


BACKEND = Path(__file__).resolve().parents[2] / "src/backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from chu_ai import knowledge_admin_api as api
from chu_ai.services.knowledge_admin_service import KnowledgeConflict


SECRET = "unit-test-admin-key-keep-private"
VERSION = "a" * 32
PREVIEW = "b" * 32
JOB = "c" * 32
PREFIX = "/api/admin/knowledge"


class KnowledgeAdminApiTests(TestCase):
    def setUp(self):
        self.manager = mock.Mock()
        self.manager.status.return_value = {
            "initialized": True, "initialization_required": False,
            "active_version": VERSION, "last_updated": "2026-11-08T00:00:00+00:00",
            "documents": [{"id": "opening.md", "title": "開館時間"}],
            "history": [], "busy": False,
        }
        self.manager.document.return_value = {"id": "opening.md", "title": "開館時間", "content": "# 開館時間\n平日は18時まで。"}
        self.manager.preview.return_value = {"preview_id": PREVIEW, "base_version": VERSION, "title": "開館時間", "content": "確認する本文", "warnings": []}
        self.manager.publish.return_value = JOB
        self.manager.restore.return_value = JOB
        self.manager.initialize.return_value = JOB
        self.manager.job.return_value = {"id": JOB, "status": "running", "stage": "embedding", "message": "検索データを準備しています。", "progress": 50}
        self.key_patch = mock.patch.object(api, "CHAT_LOG_ADMIN_API_KEY", SECRET)
        self.manager_patch = mock.patch.object(api, "get_knowledge_manager", return_value=self.manager)
        self.key_patch.start()
        self.manager_patch.start()
        self.addCleanup(self.key_patch.stop)
        self.addCleanup(self.manager_patch.stop)
        app = FastAPI()
        app.include_router(api.router)
        self.client = TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.headers = {"X-Admin-Key": SECRET}

    def test_every_route_requires_header_key_and_rejects_query_key(self):
        paths = [
            ("get", "/status", None), ("get", "/documents/opening.md", None),
            ("get", "/jobs/" + JOB, None), ("post", "/initialize", None),
            ("post", "/preview", {"title": "案内", "content": "本文"}),
            ("post", "/publish", {"preview_id": PREVIEW, "base_version": VERSION}),
            ("post", "/restore", {"version_id": VERSION, "base_version": VERSION}),
        ]
        for method, path, body in paths:
            for headers in [{}, {"X-Admin-Key": "incorrect"}]:
                with self.subTest(method=method, path=path, key=bool(headers)):
                    response = self.client.request(method, PREFIX + path, headers=headers, **({"json": body} if body is not None else {}))
                    self.assertEqual(response.status_code, 401, response.text)
                    self.assertNotIn(SECRET, response.text)
        response = self.client.get(PREFIX + "/status", params={"key": SECRET})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.manager.mock_calls, [])

    def test_unconfigured_key_returns_setup_message(self):
        with mock.patch.object(api, "CHAT_LOG_ADMIN_API_KEY", ""):
            response = self.client.get(PREFIX + "/status", headers=self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertIn("初回セットアップ", response.json()["detail"])
        self.manager.status.assert_not_called()

    def test_remote_host_and_foreign_origins_are_forbidden(self):
        for host in ["example.com", "192.168.1.20:8000", "127.0.0.1.evil.example:8000"]:
            with self.subTest(host=host):
                response = self.client.get(PREFIX + "/status", headers={**self.headers, "Host": host})
                self.assertEqual(response.status_code, 403, response.text)
        for origin in ["https://evil.example", "null", "http://localhost", "http://127.0.0.1:8000", "http://127.0.0.1.evil.example"]:
            with self.subTest(origin=origin):
                response = self.client.get(PREFIX + "/status", headers={**self.headers, "Origin": origin})
                self.assertEqual(response.status_code, 403, response.text)
        self.manager.status.assert_not_called()

    def test_malformed_origin_is_forbidden_without_unhandled_error(self):
        for origin in ["http://[", "http://[invalid]", "http://[::1"]:
            with self.subTest(origin=origin):
                response = self.client.get(PREFIX + "/status", headers={**self.headers, "Origin": origin})
                self.assertEqual(response.status_code, 403, response.text)

    def test_status_allows_same_origin_loopback_hosts_and_port(self):
        for host in ["127.0.0.1", "localhost", "127.0.0.1:8000", "[::1]:8000"]:
            with self.subTest(host=host):
                response = self.client.get(PREFIX + "/status", headers={**self.headers, "Host": host, "Origin": "http://" + host})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["active_version"], VERSION)
        self.assertEqual(self.manager.status.call_count, 4)

    def test_document_and_job_responses_are_forwarded(self):
        response = self.client.get(PREFIX + "/documents/opening.md", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertIn("18時", response.json()["content"])
        self.manager.document.assert_called_once_with("opening.md")
        response = self.client.get(PREFIX + "/jobs/" + JOB, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "running")
        self.manager.job.assert_called_once_with(JOB)

    def test_base64_japanese_markdown_preview_decodes_bytes(self):
        data = "# 開館時間\n\n平日は18時まで。".encode("utf-8")
        response = self.client.post(PREFIX + "/preview", headers=self.headers, json={
            "filename": "開館時間.md", "content_base64": base64.b64encode(data).decode("ascii"), "target_id": "opening.md",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["preview_id"], PREVIEW)
        self.manager.preview.assert_called_once_with(filename="開館時間.md", data=data, target_id="opening.md")

    def test_edit_preview_forwards_original_preview_id(self):
        response = self.client.post(PREFIX + "/preview", headers=self.headers, json={
            "title": "開館時間 改定版", "content": "確認後の本文", "target_id": "opening.md", "source_preview_id": PREVIEW,
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.manager.preview.assert_called_once_with(title="開館時間 改定版", content="確認後の本文", target_id="opening.md", source_preview_id=PREVIEW)

    def test_invalid_base64_and_partial_file_request_are_rejected(self):
        bodies = [
            {"filename": "案内.md", "content_base64": "not base64!!"},
            {"filename": "案内.md"}, {"content_base64": "YQ=="},
            {"filename": "", "content_base64": "YQ=="},
        ]
        for body in bodies:
            with self.subTest(body=body):
                response = self.client.post(PREFIX + "/preview", headers=self.headers, json=body)
                self.assertEqual(response.status_code, 400, response.text)
        self.manager.preview.assert_not_called()

    def test_file_and_edit_input_modes_cannot_be_mixed(self):
        for field, value in [("content", "別の本文"), ("title", "別のタイトル"), ("source_preview_id", PREVIEW)]:
            with self.subTest(field=field):
                response = self.client.post(PREFIX + "/preview", headers=self.headers, json={
                    "filename": "案内.md", "content_base64": "YQ==", field: value,
                })
                self.assertEqual(response.status_code, 400, response.text)
        self.manager.preview.assert_not_called()

    def test_decoded_upload_larger_than_eight_mib_is_rejected(self):
        payload = base64.b64encode(b"a" * (api.MAX_UPLOAD_BYTES + 1)).decode("ascii")
        response = self.client.post(PREFIX + "/preview", headers=self.headers, json={"filename": "大きな資料.txt", "content_base64": payload})
        self.assertEqual(response.status_code, 413, response.text[:300])
        self.manager.preview.assert_not_called()

    def test_schema_limits_reject_unbounded_fields(self):
        for body in [
            {"title": "あ" * 151, "content": "本文"},
            {"title": "案内", "content": "あ" * 200001},
            {"filename": "a" * 256, "content_base64": "YQ=="},
            {"title": "案内", "content": "本文", "source_preview_id": "short"},
        ]:
            with self.subTest(fields=list(body)):
                response = self.client.post(PREFIX + "/preview", headers=self.headers, json=body)
                self.assertEqual(response.status_code, 422)
        self.manager.preview.assert_not_called()

    def test_domain_conflicts_and_validation_errors_are_friendly(self):
        self.manager.preview.side_effect = KnowledgeConflict("別の操作で更新されました。再確認してください。")
        response = self.client.post(PREFIX + "/preview", headers=self.headers, json={"title": "案内", "content": "本文"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("再確認", response.json()["detail"])
        self.manager.document.side_effect = ValueError("指定した知識が見つかりません。")
        response = self.client.get(PREFIX + "/documents/missing.md", headers=self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertIn("見つかりません", response.json()["detail"])

    def test_unexpected_backend_error_does_not_leak_credentials_in_response_or_log(self):
        dsn = "postgresql://administrator:" + SECRET + "@private-db/knowledge"
        self.manager.status.side_effect = RuntimeError("Database connection failed: " + dsn)
        output = StringIO()
        with redirect_stdout(output):
            response = self.client.get(PREFIX + "/status", headers=self.headers)
        self.assertEqual(response.status_code, 503)
        for text in [response.text, output.getvalue()]:
            self.assertNotIn(SECRET, text)
            self.assertNotIn(dsn, text)
            self.assertNotIn("private-db", text)
        self.assertIn("初期設定", response.json()["detail"])

    def test_publish_restore_and_initialize_queue_jobs(self):
        response = self.client.post(PREFIX + "/publish", headers=self.headers, json={"preview_id": PREVIEW, "base_version": VERSION})
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json(), {"job_id": JOB})
        self.manager.publish.assert_called_once_with(PREVIEW, VERSION)
        response = self.client.post(PREFIX + "/restore", headers=self.headers, json={"version_id": VERSION, "base_version": VERSION})
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json(), {"job_id": JOB})
        self.manager.restore.assert_called_once_with(VERSION, VERSION)
        response = self.client.post(PREFIX + "/initialize", headers=self.headers)
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json(), {"job_id": JOB})
        self.manager.initialize.assert_called_once_with()


if __name__ == "__main__":
    main()
