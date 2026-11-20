"""設置PCの資料・原本・履歴とおみくじ集計をファイルに持ち出す。"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime

from pydantic import BaseModel, Field, StrictBool, StrictInt

from chu_ai.services.knowledge_admin_service import KnowledgeManager, _document_id, _now, _record_path
from chu_ai.services.knowledge_import_service import MAX_UPLOAD_BYTES


MAX_BACKUP_BYTES = 64 * 1024 * 1024
MAX_RESTORED_BYTES = 256 * 1024 * 1024
TALLY_IDS = ("super-daikichi", "daikichi", "kichi", "chukichi", "shokichi", "suekichi")
MAX_TALLY = 2**53 - 1
INVALID_BACKUP = "保存した内容を読み取れませんでした。コモ管理で保存したファイルを選び直してください。"


class Document(BaseModel):
    id: str
    title: str = Field(min_length=1, max_length=150)
    source_filename: str = Field(min_length=1, max_length=255)
    original: str | None = None
    origin: str = Field(min_length=1, max_length=80)
    updated_at: datetime
    enabled: StrictBool = True
    body: str


class Version(BaseModel):
    created_at: datetime
    label: str = Field(min_length=1, max_length=250)
    documents: list[Document] = Field(min_length=1, max_length=10000)


class Backup(BaseModel):
    format: str
    created_at: datetime
    versions: list[Version] = Field(min_length=1, max_length=100)
    bodies: dict[str, str]
    originals: dict[str, str]
    tally: dict[str, StrictInt] | None = None


def validate_tally(counts: dict[str, int] | None) -> dict[str, int] | None:
    if counts is None:
        return None
    if (set(counts) != set(TALLY_IDS)
            or any(type(value) is not int or not 0 <= value <= MAX_TALLY for value in counts.values())
            or sum(counts.values()) > MAX_TALLY):
        raise ValueError("おみくじの回数を読み取れません。")
    return counts


def create_backup(manager: KnowledgeManager, tally: dict[str, int] | None) -> bytes:
    active = manager.read_active()
    if not active:
        raise ValueError("最初の案内を準備してから保存してください。")
    archive = {
        "format": "chat-como-backup-1", "created_at": _now(),
        "versions": [], "bodies": {}, "originals": {}, "tally": validate_tally(tally),
    }
    for entry in reversed(manager.history(active, limit=100)):
        directory, manifest = manager._version(entry["id"])
        version = {"created_at": manifest["created_at"], "label": manifest["label"], "documents": []}
        for item in manifest["documents"]:
            content = _record_path(directory, item).read_text(encoding="utf-8")
            body = hashlib.sha256(content.encode("utf-8")).hexdigest()
            archive["bodies"][body] = content
            original = item.get("original")
            if original and original not in archive["originals"]:
                if not re.fullmatch(r"[0-9a-f]{64}\.(docx|txt|md|markdown)", original):
                    raise ValueError(INVALID_BACKUP)
                data = (manager.root / "originals" / original).read_bytes()
                archive["originals"][original] = base64.b64encode(data).decode("ascii")
            version["documents"].append({**item, "body": body})
        archive["versions"].append(version)
    output = json.dumps(archive, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(output) > MAX_BACKUP_BYTES:
        raise ValueError("保存する内容が多すぎます。担当者に保存方法を確かめてください。")
    read_backup(output)
    return output


def read_backup(data: bytes) -> Backup:
    try:
        archive = Backup.model_validate_json(data)
        if archive.format != "chat-como-backup-1":
            raise ValueError
        validate_tally(archive.tally)
        sizes = {}
        for digest, body in archive.bodies.items():
            encoded_body = body.encode("utf-8")
            if (not body.strip() or len(body) > 200001 or "\x00" in body
                    or hashlib.sha256(encoded_body).hexdigest() != digest):
                raise ValueError
            sizes[digest] = len(encoded_body)
        for name, encoded in archive.originals.items():
            if not re.fullmatch(r"[0-9a-f]{64}\.(docx|txt|md|markdown)", name):
                raise ValueError
            original = base64.b64decode(encoded, validate=True)
            if len(original) > MAX_UPLOAD_BYTES or hashlib.sha256(original).hexdigest() != name.split(".")[0]:
                raise ValueError
        restored_bytes = 0
        for version in archive.versions:
            ids = set()
            if not any(item.enabled for item in version.documents):
                raise ValueError
            for item in version.documents:
                document_id = _document_id(item.id).casefold()
                if (document_id in ids or item.body not in archive.bodies
                        or item.original is not None and item.original not in archive.originals):
                    raise ValueError
                ids.add(document_id)
                restored_bytes += sizes[item.body]
            if restored_bytes > MAX_RESTORED_BYTES:
                raise ValueError
        return archive
    except (ValueError, RecursionError) as error:
        raise ValueError(INVALID_BACKUP) from error


def restore_backup(manager: KnowledgeManager, data: bytes, base: str | None) -> str:
    archive = read_backup(data)

    def build():
        for name, encoded in archive.originals.items():
            manager.save_original(name, base64.b64decode(encoded))
        parent = base
        for version in archive.versions:
            items = [(item.model_dump(mode="json", exclude={"body"}), archive.bodies[item.body])
                     for item in version.documents]
            parent, _ = manager._make_version(parent, version.label, items, version.created_at.isoformat())
        # Link the imported history to the original local version. The indexer
        # makes the new snapshot active only after the search data commits.
        return manager._snapshot(parent, "保存した内容に戻す")

    return manager._queue("backup", base, build)
