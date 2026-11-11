from io import BytesIO
from pathlib import Path
import tempfile
import time
import unittest
from zipfile import ZipFile

from chu_ai.knowledge_template import create_knowledge_template
from chu_ai.services.knowledge_import_service import import_knowledge_file
from chu_ai.services.knowledge_admin_service import KnowledgeManager


class TemplateTests(unittest.TestCase):
    def test_word_template_is_readable_and_contains_editable_sections(self):
        data = create_knowledge_template()
        with ZipFile(BytesIO(data)) as archive:
            self.assertIn('word/styles.xml', archive.namelist())
        document = import_knowledge_file('知識更新.docx', data)
        self.assertIn('【記入欄】', document['title'])
        for text in ('案内する内容', '対象と期間', '確認先'):
            self.assertIn(text, document['content'])

    def test_unfilled_template_cannot_publish_and_title_is_searchable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed = root / 'seed'
            seed.mkdir()
            (seed / '01_sample.md').write_text('# 初期知識\n本文', encoding='utf-8')
            active = [None]
            def index(directory, version, expected, progress):
                active[0] = version
                return {}
            manager = KnowledgeManager(root / 'runtime', seed, lambda: active[0], index)
            job_id = manager.initialize()
            for _ in range(100):
                if manager.job(job_id)['status'] == 'succeeded' and not manager._busy_job():
                    break
                time.sleep(0.01)
            else:
                self.fail('initialization did not finish')
            preview = manager.preview(filename='資料.docx', data=create_knowledge_template())
            with self.assertRaisesRegex(ValueError, '記入欄'):
                manager.publish(preview['preview_id'], preview['base_version'])
            edited = manager.preview(title='新しい開館時間', content='# 古い見出し\n\n18時まで開館します。')
            self.assertEqual(edited['content'], '# 新しい開館時間\n\n18時まで開館します。')
            plain = manager.preview(title='貸出方法', content='学生証を受付へ。')
            self.assertEqual(plain['content'], '# 貸出方法\n\n学生証を受付へ。')


if __name__ == '__main__':
    unittest.main()
