"""Word/USBの本文取り込みを、外部APIや追加依存なしで検証する。"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from unittest import TestCase, main, mock
import importlib.util
import warnings
import zipfile


SERVICE = Path(__file__).resolve().parents[2] / "src/backend/chu_ai/services/knowledge_import_service.py"
SPEC = importlib.util.spec_from_file_location("knowledge_import_service", SERVICE)
service = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(service)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def word(body: str = "", *, styles: str | None = None, extras: dict | None = None, document: bytes | None = None) -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        archive.writestr("word/document.xml", document if document is not None else f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>')
        if styles is not None:
            archive.writestr("word/styles.xml", f'<w:styles xmlns:w="{W}">{styles}</w:styles>')
        for name, data in (extras or {}).items():
            archive.writestr(name, data)
    return output.getvalue()


def paragraph(text: str, style: str = "") -> str:
    properties = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{properties}<w:r><w:t>{text}</w:t></w:r></w:p>"


class KnowledgeImportTests(TestCase):
    def test_unicode_windows_filename_and_utf8_bom(self):
        result = service.import_knowledge_file("E:\\コモ更新用\\開館時間.MD", "\ufeff# 開館時間\r\n\r\n平日は18時まで。\r\n".encode())
        self.assertEqual(result["source_filename"], "開館時間.MD")
        self.assertEqual(result["title"], "開館時間")
        self.assertEqual(result["content"], "# 開館時間\n\n平日は18時まで。")
        self.assertEqual(result["warnings"], [])

    def test_plain_text_title_and_shift_jis_warning(self):
        result = service.import_knowledge_file("貸出ルール.txt", "学生証を持参してください。".encode("cp932"))
        self.assertEqual(result["title"], "貸出ルール")
        self.assertEqual(result["content"], "学生証を持参してください。")
        self.assertIn("Shift_JIS", result["warnings"][0])

    def test_utf16_bom(self):
        result = service.import_knowledge_file("案内.txt", "日本語のご案内".encode("utf-16"))
        self.assertEqual(result["content"], "日本語のご案内")

    def test_markdown_is_preserved(self):
        text = "# 利用方法\n\n## 貸出\n- 学生証\n- 返却日\n\n| 項目 | 値 |\n| --- | --- |\n| 場所 | 受付 |"
        self.assertEqual(service.import_knowledge_file("案内.md", text.encode())["content"], text)

    def test_word_heading_body_and_table_remain_in_document_order(self):
        table = "<w:tbl><w:tr><w:tc>" + paragraph("曜日") + "</w:tc><w:tc>" + paragraph("時間") + "</w:tc></w:tr><w:tr><w:tc>" + paragraph("平日") + "</w:tc><w:tc>" + paragraph("9時〜18時") + "</w:tc></w:tr></w:tbl>"
        body = paragraph("開館時間", "Title") + paragraph("通常", "Heading2") + paragraph("本文の前半。") + table + paragraph("最終入館は17時30分。")
        result = service.import_knowledge_file("無題.docx", word(body))
        self.assertEqual(result["title"], "開館時間")
        self.assertIn("# 開館時間\n\n## 通常", result["content"])
        self.assertIn("| 曜日 | 時間 |\n| --- | --- |\n| 平日 | 9時〜18時 |", result["content"])
        self.assertLess(result["content"].index("本文の前半"), result["content"].index("| 曜日"))
        self.assertLess(result["content"].index("| 平日"), result["content"].index("最終入館"))

    def test_localized_heading_style_and_content_controls(self):
        styles = '<w:style w:styleId="CustomHeading"><w:name w:val="見出し 2"/></w:style>'
        body = '<w:sdt><w:sdtContent>' + paragraph("持ち物", "CustomHeading") + paragraph("学生証と筆記用具") + "</w:sdtContent></w:sdt>"
        result = service.import_knowledge_file("持ち物.docx", word(body, styles=styles))
        self.assertEqual(result["content"], "## 持ち物\n\n学生証と筆記用具")

    def test_word_breaks_tabs_and_hyperlink_visible_text(self):
        body = '<w:p><w:r><w:t>受付</w:t><w:tab/><w:t>1階</w:t><w:br/><w:t>詳細は</w:t></w:r><w:hyperlink><w:r><w:t>案内ページ</w:t></w:r></w:hyperlink></w:p>'
        self.assertEqual(service.import_knowledge_file("案内.docx", word(body))["content"], "受付\t1階\n詳細は案内ページ")

    def test_table_keeps_merged_cells_nested_tables_and_pipe_characters(self):
        nested = "<w:tbl><w:tr><w:tc>" + paragraph("内部の情報") + "</w:tc></w:tr></w:tbl>"
        body = '<w:tbl><w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr>' + paragraph("A|B") + paragraph("二行目") + '</w:tc></w:tr><w:tr><w:tc>' + paragraph("次") + '</w:tc><w:tc>' + nested + "</w:tc></w:tr></w:tbl>"
        content = service.import_knowledge_file("表.docx", word(body))["content"]
        self.assertIn("| A\\|B<br>二行目 |  |", content)
        self.assertIn("| 次 | 内部の情報 |", content)

    def test_images_and_body_external_content_are_warned_and_not_invented(self):
        body = paragraph("読み取れる本文") + '<w:p><w:r><w:drawing/></w:r><w:pict><w:txbxContent>' + paragraph("図形の秘密") + '</w:txbxContent></w:pict></w:p>'
        result = service.import_knowledge_file("図入り.docx", word(body, extras={"word/media/image1.png": b"not-a-real-image", "word/header1.xml": b"<header/>"}))
        self.assertEqual(result["content"], "読み取れる本文")
        self.assertTrue(any("画像" in warning for warning in result["warnings"]))
        self.assertTrue(any("ヘッダー" in warning for warning in result["warnings"]))

    def test_tracked_deleted_text_is_excluded_and_insertions_are_warned(self):
        body = '<w:p><w:r><w:t>現在は</w:t></w:r><w:del><w:r><w:delText>17時</w:delText></w:r></w:del><w:ins><w:r><w:t>18時</w:t></w:r></w:ins><w:r><w:t>まで。</w:t></w:r></w:p>'
        result = service.import_knowledge_file("変更履歴.docx", word(body))
        self.assertEqual(result["content"], "現在は18時まで。")
        self.assertTrue(any("変更履歴" in warning for warning in result["warnings"]))

    def test_empty_content_and_binary_text_are_rejected(self):
        for filename, data in [("空.txt", b" \r\n\t"), ("空.docx", word()), ("空.txt", b""), ("バイナリ.txt", b"abc\x00def")]:
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                service.import_knowledge_file(filename, data)

    def test_unsupported_files_old_word_and_invalid_names_are_rejected(self):
        for filename in ["資料.pdf", "資料.xlsx", "資料.docm", "資料.doc", "資料.exe", "", "\x00.txt"]:
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                service.import_knowledge_file(filename, b"content")

    def test_content_and_upload_limits(self):
        self.assertEqual(len(service.import_knowledge_file("最大.txt", ("あ" * service.MAX_CONTENT_CHARS).encode())["content"]), service.MAX_CONTENT_CHARS)
        with self.assertRaisesRegex(ValueError, "20万"):
            service.import_knowledge_file("長すぎ.txt", ("あ" * (service.MAX_CONTENT_CHARS + 1)).encode())
        with self.assertRaisesRegex(ValueError, "8MB"):
            service.import_knowledge_file("大きすぎ.txt", b"a" * (service.MAX_UPLOAD_BYTES + 1))

    def test_corrupt_archive_wrong_xml_and_missing_docx_parts(self):
        for data in [b"not zip", word(document=b"<invalid>"), word(document=b"<other/>"), word(document=f'<w:document xmlns:w="{W}"/>'.encode())]:
            with self.subTest(data=data[:15]), self.assertRaises(ValueError):
                service.import_knowledge_file("壊れた.docx", data)
        output = BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("anything.txt", "not word")
        with self.assertRaises(ValueError):
            service.import_knowledge_file("偽装.docx", output.getvalue())

    def test_dtd_and_entity_declarations_including_utf16_are_rejected(self):
        for prefix in ['<!DOCTYPE w:document [<!ENTITY text "anything">]>', '<!DOCTYPE w:document SYSTEM "file:///etc/passwd">']:
            xml = prefix + f'<w:document xmlns:w="{W}"><w:body>{paragraph("test")}</w:body></w:document>'
            for encoding in ["utf-8", "utf-16"]:
                with self.subTest(encoding=encoding), self.assertRaisesRegex(ValueError, "XML定義"):
                    service.import_knowledge_file("定義.docx", word(document=xml.encode(encoding)))

    def test_archive_path_traversal_absolute_symlink_and_duplicate_members_are_rejected(self):
        for name in ["../outside.xml", "/outside.xml", "C:/outside.xml", "word/../../outside.xml", "word\\..\\outside.xml"]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                service.import_knowledge_file("危険.docx", word(paragraph("本文"), extras={name: b"x"}))
        output = BytesIO(word(paragraph("本文")))
        with zipfile.ZipFile(output, "a") as archive:
            info = zipfile.ZipInfo("word/link.xml")
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            archive.writestr(info, "outside")
        with self.assertRaises(ValueError):
            service.import_knowledge_file("リンク.docx", output.getvalue())
        output = BytesIO(word(paragraph("本文")))
        with zipfile.ZipFile(output, "a") as archive, warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            archive.writestr("word/document.xml", "duplicate")
        with self.assertRaises(ValueError):
            service.import_knowledge_file("重複.docx", output.getvalue())

    def test_archive_entry_and_expanded_size_limits(self):
        with mock.patch.object(service, "MAX_ARCHIVE_ENTRIES", 2):
            with self.assertRaises(ValueError):
                service.import_knowledge_file("多すぎ.docx", word(paragraph("本文"), extras={"extra": b"x"}))
        with mock.patch.object(service, "MAX_ARCHIVE_BYTES", 100):
            with self.assertRaises(ValueError):
                service.import_knowledge_file("膨張.docx", word(paragraph("本文")))

    def test_xml_depth_element_and_xml_size_limits(self):
        nested = '<w:customXml>' * 70 + paragraph("本文") + '</w:customXml>' * 70
        with self.assertRaisesRegex(ValueError, "複雑"):
            service.import_knowledge_file("深すぎ.docx", word(nested))
        with mock.patch.object(service, "MAX_XML_ELEMENTS", 4):
            with self.assertRaises(ValueError):
                service.import_knowledge_file("要素.docx", word(paragraph("本文")))
        with mock.patch.object(service, "MAX_XML_BYTES", 100):
            with self.assertRaises(ValueError):
                service.import_knowledge_file("XML.docx", word(paragraph("本文")))


if __name__ == "__main__":
    main()
