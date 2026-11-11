"""USB等から選んだWord・テキストを知識用Markdownへ変換する。

変換中にファイルを展開・実行したり、外部リンクを取得したりしない。
保存・反映は呼び出し元がプレビューの確認後に行う。
"""

from __future__ import annotations

from io import BytesIO
from pathlib import PurePosixPath
import re
import stat
import zipfile
import xml.etree.ElementTree as ET


MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_CONTENT_CHARS = 200_000
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 2048
MAX_XML_BYTES = 8 * 1024 * 1024
MAX_XML_ELEMENTS = 200_000
MAX_XML_DEPTH = 64

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_NS = {"w": _W[1:-1]}
_IGNORED_TEXT = {_W + tag for tag in ("del", "moveFrom", "drawing", "pict", "object", "txbxContent")}


def _filename(filename: str) -> str:
    if not isinstance(filename, str):
        raise ValueError("ファイル名を確認してください。")
    name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name or name in {".", ".."} or len(name) > 255 or any(ord(char) < 32 for char in name):
        raise ValueError("ファイル名を確認してください。短い名前に変更してもう一度選んでください。")
    return name


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise ValueError("読み取れる本文がありません。文字の入ったファイルを選んでください。")
    if any(ord(char) < 32 and char not in "\n\t" for char in text):
        raise ValueError("本文として読み取れないデータが含まれています。Wordまたはテキスト形式で保存し直してください。")
    if len(text) > MAX_CONTENT_CHARS:
        raise ValueError("本文が長すぎます。20万文字以内になるよう、テーマごとにファイルを分けてください。")
    return text


def _title(content: str, filename: str) -> str:
    for line in content.splitlines():
        if line.startswith("# ") and line[2:].strip():
            return line[2:].strip()[:200]
    return PurePosixPath(filename).stem[:200] or "無題の知識"


def _decode_text(data: bytes) -> tuple[str, list[str]]:
    warnings: list[str] = []
    try:
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = data.decode("utf-16")
        else:
            text = data.decode("utf-8-sig")
    except UnicodeError:
        try:
            text = data.decode("cp932")
        except UnicodeError as error:
            raise ValueError("文字コードを読み取れませんでした。UTF-8のテキスト形式で保存し直してください。") from error
        warnings.append("Windows日本語（Shift_JIS）として読み取りました。文字化けがないか、内容を確認してください。")
    return _normalize_text(text), warnings


def _check_archive(archive: zipfile.ZipFile) -> set[str]:
    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_ENTRIES or sum(info.file_size for info in infos) > MAX_ARCHIVE_BYTES:
        raise ValueError("Wordファイルの中身が大きすぎます。画像や不要なページを減らして保存し直してください。")
    names: set[str] = set()
    for info in infos:
        name = info.filename
        normalized = name.replace("\\", "/")
        if (
            normalized.startswith("/")
            or re.match(r"^[A-Za-z]:", normalized)
            or ".." in PurePosixPath(normalized).parts
            or "\x00" in name
            or stat.S_ISLNK(info.external_attr >> 16)
            or name in names
        ):
            raise ValueError("Wordファイルの構造を確認できません。Wordで新しい.docxとして保存し直してください。")
        if info.flag_bits & 1:
            raise ValueError("暗号化されたWordファイルは読み取れません。パスワードを外した.docxを選んでください。")
        if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise ValueError("このWordファイルの圧縮形式には対応していません。Wordで保存し直してください。")
        names.add(name)
    if not {"[Content_Types].xml", "word/document.xml"}.issubset(names):
        raise ValueError("Word形式のファイルではありません。.docx形式で保存し直してください。")
    return names


def _read_xml(archive: zipfile.ZipFile, name: str) -> ET.Element:
    if archive.getinfo(name).file_size > MAX_XML_BYTES:
        raise ValueError("Wordの本文または書式情報が大きすぎます。ファイルを分けてください。")
    data = archive.read(name)
    declarations = data.replace(b"\x00", b"").upper()
    if b"<!DOCTYPE" in declarations or b"<!ENTITY" in declarations:
        raise ValueError("このWordファイルには対応していないXML定義が含まれています。Wordで保存し直してください。")
    try:
        iterator = ET.iterparse(BytesIO(data), events=("start", "end"))
        count = 0
        depth = 0
        for event, _ in iterator:
            if event == "start":
                count += 1
                depth += 1
                if count > MAX_XML_ELEMENTS or depth > MAX_XML_DEPTH:
                    raise ValueError("Wordの構造が複雑すぎます。テーマごとにファイルを分けてください。")
            else:
                depth -= 1
        return iterator.root
    except ET.ParseError as error:
        raise ValueError("Wordの本文を読み取れません。Wordで開けることを確認して保存し直してください。") from error


def _style_levels(archive: zipfile.ZipFile, names: set[str]) -> dict[str, int]:
    levels: dict[str, int] = {"Title": 1, "Subtitle": 2}
    if "word/styles.xml" not in names:
        return levels
    for style in _read_xml(archive, "word/styles.xml").findall("w:style", _NS):
        style_id = style.get(_W + "styleId", "")
        name_node = style.find("w:name", _NS)
        name = name_node.get(_W + "val", "") if name_node is not None else ""
        for candidate in (style_id, name):
            normalized = re.sub(r"[\s_-]+", "", candidate).casefold()
            if normalized in {"title", "タイトル", "表題"}:
                levels[style_id] = 1
            elif normalized in {"subtitle", "サブタイトル", "副題"}:
                levels[style_id] = 2
            else:
                match = re.fullmatch(r"(?:heading|見出し)([1-9])", normalized)
                if match:
                    levels[style_id] = min(int(match[1]), 6)
    return levels


def _text(node: ET.Element) -> str:
    if node.tag in _IGNORED_TEXT:
        return ""
    if node.tag == _W + "t":
        return node.text or ""
    if node.tag == _W + "tab":
        return "\t"
    if node.tag in {_W + "br", _W + "cr"}:
        return "\n"
    if node.tag in {_W + "noBreakHyphen", _W + "softHyphen"}:
        return "-"
    return "".join(_text(child) for child in node)


def _blocks(node: ET.Element):
    for child in node:
        if child.tag in {_W + "p", _W + "tbl"}:
            yield child
        elif child.tag in {_W + value for value in ("sdt", "sdtContent", "customXml", "ins", "moveTo")}:
            yield from _blocks(child)


def _paragraph(node: ET.Element, levels: dict[str, int]) -> str:
    text = _text(node).strip()
    if not text:
        return ""
    properties = node.find("w:pPr", _NS)
    if properties is not None:
        style = properties.find("w:pStyle", _NS)
        if style is not None:
            style_id = style.get(_W + "val", "")
            level = levels.get(style_id)
            if level is None:
                match = re.fullmatch(r"Heading([1-9])", style_id, re.IGNORECASE)
                level = min(int(match[1]), 6) if match else None
            if level:
                return f"{'#' * level} {text}"
        numbering = properties.find("w:numPr", _NS)
        if numbering is not None:
            return f"- {text}"
    return text


def _cell_text(cell: ET.Element) -> str:
    parts: list[str] = []
    for block in _blocks(cell):
        if block.tag == _W + "p":
            text = _text(block).strip()
            if text:
                parts.append(text)
        else:
            for row in _table_rows(block):
                parts.append(" / ".join(row))
    return "\n".join(parts)


def _table_rows(table: ET.Element) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in table.findall("w:tr", _NS):
        cells: list[str] = []
        for cell in row.findall("w:tc", _NS):
            cells.append(_cell_text(cell))
            span = cell.find("w:tcPr/w:gridSpan", _NS)
            if span is not None:
                try:
                    span_count = int(span.get(_W + "val", "1"))
                except ValueError:
                    span_count = 1
                if span_count > 100 or span_count < 1:
                    raise ValueError("表の列数を読み取れません。表を簡単な形にして保存し直してください。")
                cells.extend([""] * (span_count - 1))
            if len(cells) > 100:
                raise ValueError("表の列数が多すぎます。100列以内の表に分けて保存し直してください。")
        if cells:
            rows.append(cells)
    return rows


def _table(table: ET.Element) -> str:
    rows = _table_rows(table)
    if not rows or not any(cell.strip() for row in rows for cell in row):
        return ""
    columns = max(map(len, rows))
    lines: list[str] = []
    for index, row in enumerate(rows):
        padded = row + [""] * (columns - len(row))
        escaped = [value.replace("|", "\\|").replace("\n", "<br>") for value in padded]
        lines.append("| " + " | ".join(escaped) + " |")
        if index == 0:
            lines.append("| " + " | ".join(["---"] * columns) + " |")
    return "\n".join(lines)


def _decode_docx(data: bytes) -> tuple[str, list[str]]:
    warnings: list[str] = []
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            names = _check_archive(archive)
            document = _read_xml(archive, "word/document.xml")
            if document.tag != _W + "document":
                raise ValueError("Wordの本文形式を確認できません。Wordで保存し直してください。")
            body = document.find("w:body", _NS)
            if body is None:
                raise ValueError("Wordファイルに本文がありません。")
            levels = _style_levels(archive, names)
            parts = [_paragraph(block, levels) if block.tag == _W + "p" else _table(block) for block in _blocks(body)]
            content = _normalize_text("\n\n".join(part for part in parts if part))
            tags = {element.tag for element in document.iter()}
            if any(name.startswith(("word/media/", "word/embeddings/", "word/charts/")) for name in names) or tags.intersection({_W + "drawing", _W + "pict", _W + "object", _W + "txbxContent", _W + "altChunk"}):
                warnings.append("画像・図形・テキストボックス・埋め込み資料の内容は取り込んでいません。必要な情報を本文に入力し、プレビューで確認してください。")
            if any(re.match(r"word/(?:header\d*|footer\d*|footnotes|endnotes)\.xml$", name) for name in names):
                warnings.append("ヘッダー・フッター・脚注は取り込んでいません。必要な情報が本文にあるか確認してください。")
            if tags.intersection({_W + "ins", _W + "del", _W + "moveFrom", _W + "moveTo"}):
                warnings.append("変更履歴があります。追加後の文章を取り込んでいます。意図した内容かプレビューで確認してください。")
            if any(tag.endswith("}oMath") or tag.endswith("}oMathPara") for tag in tags):
                warnings.append("数式は取り込んでいません。必要な内容を本文の文章として入力してください。")
            return content, warnings
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, NotImplementedError, OSError, EOFError) as error:
        raise ValueError("Wordファイルを読み取れません。暗号化されていない.docxとして保存し直してください。") from error


def import_knowledge_file(filename: str, data: bytes) -> dict:
    """ファイルを検証し、保存前プレビュー用の値だけを返す。"""
    source_filename = _filename(filename)
    extension = PurePosixPath(source_filename).suffix.casefold()
    if extension == ".doc":
        raise ValueError("古いWord（.doc）には対応していません。Wordで.docx形式として保存し直してください。")
    if extension not in {".docx", ".txt", ".md"}:
        raise ValueError("対応形式はWord（.docx）・テキスト（.txt）・Markdown（.md）です。対応形式のファイルを選んでください。")
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ValueError("ファイルが空です。本文の入ったファイルを選んでください。")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("ファイルは8MBまでです。画像や不要なページを減らすか、ファイルを分けてください。")
    content, warnings = _decode_docx(data) if extension == ".docx" else _decode_text(data)
    return {
        "source_filename": source_filename,
        "title": _title(content, source_filename),
        "content": content,
        "warnings": warnings,
    }
