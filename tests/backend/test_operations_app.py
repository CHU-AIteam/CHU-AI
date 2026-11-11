"""本体アプリへの管理画面・テンプレート・kiosk設定の接続を確認する。"""
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from chu_ai import api
from chu_ai.services.knowledge_import_service import import_knowledge_file


class OperationsAppTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(api.app, base_url='http://127.0.0.1')

    def test_management_and_print_guide_are_served(self):
        response = self.client.get('/manage/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('知識', response.text)
        self.assertEqual(self.client.get('/manage/manage.js').status_code, 200)
        self.assertEqual(self.client.get('/manage/manage.css').status_code, 200)
        guide = self.client.get('/manage/guide.html')
        self.assertEqual(guide.status_code, 200)
        self.assertIn('USB', guide.text)

    def test_word_template_route_is_not_shadowed_by_static_mount(self):
        response = self.client.get('/manage/knowledge-template.docx')
        self.assertEqual(response.status_code, 200)
        self.assertIn('wordprocessingml', response.headers['content-type'])
        self.assertIn('attachment', response.headers['content-disposition'])
        self.assertIn('案内する内容', import_knowledge_file('template.docx', response.content)['content'])

    def test_health_exposes_kiosk_setting_but_not_admin_key(self):
        with patch.object(api, 'COMO_KIOSK_AUTO_LOGIN', True):
            response = self.client.get('/api/health')
        self.assertTrue(response.json()['kiosk_auto_login'])
        self.assertNotIn('CHAT_LOG_ADMIN_API_KEY', response.text)


if __name__ == '__main__':
    unittest.main()
