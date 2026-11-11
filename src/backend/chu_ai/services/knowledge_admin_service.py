"""設置PCの知識をプレビューし、検索データと同時に公開する運用サービス。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from chu_ai.config import KNOWLEDGE_DIR, KNOWLEDGE_INDEX_FILE, KNOWLEDGE_RUNTIME_DIR


class KnowledgeConflict(ValueError):
    """再確認が必要な競合・実行中の操作。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(path: Path, value: str | dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, ensure_ascii=False, indent=2) if isinstance(value, dict) else value
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as out:
            name = out.name
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (OSError, ValueError) as error:
        raise ValueError("保存された資料を読み取れません。担当者に保存先の確認を依頼してください。") from error


def _identifier(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("指定した更新情報が見つかりません。画面を読み直してください。")
    return value


def _document_id(value: str) -> str:
    if not value or len(value) > 200 or value.startswith(".") or "/" in value or "\\" in value or not value.endswith(".md") or value == KNOWLEDGE_INDEX_FILE:
        raise ValueError("指定した知識が見つかりません。")
    return value


def _title(content: str, fallback: str) -> str:
    for line in content.splitlines():
        if line.strip().startswith("# "):
            return line.strip()[2:].strip()[:150] or fallback
    return fallback[:150]


def _active_version() -> str | None:
    from chu_ai.services.hybrid_store_service import get_active_knowledge_version
    return get_active_knowledge_version()


def index_version(directory: Path, version_id: str, expected_version: str | None, progress: Callable) -> dict:
    """変更した文書のベクトルを先に作り、全更新を1トランザクションで公開。"""
    from chu_ai.services.embedding_service import embed_texts
    from chu_ai.services.hybrid_store_service import (
        build_document_chunks, get_indexed_document_hashes,
        ingest_knowledge_to_hybrid_store,
    )
    previous = get_indexed_document_hashes()
    payloads = []
    for path in sorted(directory.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if previous.get(path.name) == hashlib.sha256(text.encode("utf-8")).hexdigest():
            continue
        for chunk in build_document_chunks(text):
            payloads.append((f"{path.name}#{chunk.chunk_no}", chunk.body))
    embeddings = {}
    for start in range(0, len(payloads), 16):
        batch = payloads[start:start + 16]
        progress("embedding", "コモが新しい知識を探せるように準備しています。", 20 + int(start / max(1, len(payloads)) * 65))
        vectors = embed_texts([text for _, text in batch])
        if len(vectors) != len(batch):
            raise RuntimeError("Incomplete embeddings")
        embeddings.update((key, vector) for (key, _), vector in zip(batch, vectors))
    progress("publishing", "準備ができた知識へ切り替えています。", 90)
    return ingest_knowledge_to_hybrid_store(
        embeddings, knowledge_dir=directory, version_id=version_id,
        expected_version=expected_version, require_all_embeddings=True,
    )


class KnowledgeManager:
    def __init__(self, root: Path = KNOWLEDGE_RUNTIME_DIR, seed: Path = KNOWLEDGE_DIR,
                 read_active: Callable = _active_version, indexer: Callable = index_version):
        self.root, self.seed = Path(root), Path(seed)
        self.read_active, self.indexer = read_active, indexer
        self._mutex = threading.Lock()

    def _version(self, version_id: str) -> tuple[Path, dict]:
        directory = self.root / "versions" / _identifier(version_id)
        return directory, _read(directory / "manifest.json")

    def _try_lock(self) -> int | None:
        self.root.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.root / "operation.lock", os.O_CREAT | os.O_RDWR, 0o600)
        if os.fstat(fd).st_size == 0:
            os.write(fd, b" ")
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except (BlockingIOError, OSError):
            os.close(fd)
            return None

    def _unlock(self, fd: int) -> None:
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def _busy_job(self) -> str | None:
        fd = self._try_lock()
        if fd is not None:
            self._unlock(fd)
            return None
        try:
            value = json.loads((self.root / "operation.lock").read_text(encoding="utf-8"))
            return _identifier(value["job_id"])
        except (ValueError, KeyError, OSError):
            return "starting"

    def _seed_documents(self) -> list[dict]:
        documents = []
        for path in sorted(self.seed.glob("*.md")):
            if path.name == KNOWLEDGE_INDEX_FILE:
                continue
            content = path.read_text(encoding="utf-8")
            documents.append({"id": path.name, "title": _title(content, path.stem),
                              "source_filename": path.name, "origin": "初期知識", "updated_at": None})
        return documents

    def history(self, current: str | None) -> list[dict]:
        result, seen = [], set()
        cursor = current
        while cursor and cursor not in seen and len(result) < 100:
            seen.add(cursor)
            _, manifest = self._version(cursor)
            result.append({"id": cursor, "created_at": manifest["created_at"],
                           "label": manifest["label"], "document_count": len(manifest["documents"]),
                           "current": cursor == current})
            cursor = manifest.get("parent_version")
        return result

    def status(self) -> dict:
        active = self.read_active()
        manifest = self._version(active)[1] if active else None
        job = self._busy_job()
        return {"initialized": bool(active), "initialization_required": not bool(active),
                "active_version": active, "last_updated": manifest["created_at"] if manifest else None,
                "documents": manifest["documents"] if manifest else self._seed_documents(),
                "history": self.history(active), "busy": bool(job),
                "active_job": job if job != "starting" else None}

    def document(self, document_id: str) -> dict:
        _document_id(document_id)
        active = self.read_active()
        if active:
            directory, manifest = self._version(active)
            records = manifest["documents"]
            source = directory / "documents"
        else:
            records, source = self._seed_documents(), self.seed
        record = next((item for item in records if item["id"] == document_id), None)
        if not record:
            raise ValueError("指定した知識が見つかりません。画面を読み直してください。")
        return {**record, "content": (source / document_id).read_text(encoding="utf-8")}

    def preview(self, *, filename: str | None = None, data: bytes | None = None,
                title: str | None = None, content: str | None = None,
                target_id: str | None = None, source_preview_id: str | None = None) -> dict:
        from chu_ai.services.knowledge_import_service import import_knowledge_file
        active = self.read_active()
        if not active:
            raise KnowledgeConflict("最初に「初期知識を準備」を実行してください。")
        _, manifest = self._version(active)
        records = manifest["documents"]
        warnings = []
        original = None
        draft_id = None
        inherited = None
        if source_preview_id:
            inherited = _read(self.root / "previews" / (_identifier(source_preview_id) + ".json"))
            if inherited["base_version"] != active:
                raise KnowledgeConflict("知識が更新されています。ファイルを選び直して内容を再確認してください。")
            if datetime.fromisoformat(inherited["created_at"]) < datetime.now(timezone.utc) - timedelta(days=1):
                raise KnowledgeConflict("確認画面の有効期限が切れました。ファイルを選び直してください。")
            original = inherited.get("original")
            warnings = inherited.get("warnings", [])
            if inherited["action"] == "replace":
                target_id = target_id or inherited["target_id"]
            else:
                draft_id = inherited["target_id"]
        if filename is not None and data is not None:
            imported = import_knowledge_file(filename, data)
            filename, title, content = imported["source_filename"], imported["title"], imported["content"]
            warnings = imported["warnings"]
            if len(title) > 150:
                title = title[:150]
                warnings = [*warnings, "タイトルを150文字に短縮しました。内容を確認してください。"]
            original = hashlib.sha256(data).hexdigest() + Path(filename).suffix.lower()
            destination = self.root / "originals" / original
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Identical uploads share an immutable original.
            if not destination.exists():
                with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as out:
                    temporary = Path(out.name)
                    out.write(data)
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(temporary, destination)
            if not target_id:
                matches = [item for item in records if Path(item["source_filename"]).stem.casefold() == Path(filename).stem.casefold()]
                if len(matches) == 1:
                    target_id = matches[0]["id"]
                    warnings = [*warnings, "同じ名前の知識があるため、置き換えとして確認してください。"]
        else:
            filename = inherited.get("source_filename") if inherited else None
        content = (content or "").strip()
        if not content or len(content) > 200000 or "\x00" in content:
            raise ValueError("本文は空にせず、20万文字以内で入力してください。")
        title = (title or _title(content, Path(filename).stem if filename else "新しい知識")).strip()
        if not title or len(title) > 150 or "\n" in title or "\r" in title:
            raise ValueError("タイトルは改行せず、150文字以内で入力してください。")
        # The title must be searchable too, and must match the confirmed metadata.
        lines = content.splitlines()
        content = "# " + title + "\n" + "\n".join(lines[1:]) if lines[0].startswith("# ") else "# " + title + "\n\n" + content
        content = content.strip()
        if len(content) > 200000:
            raise ValueError("タイトルを含めて20万文字以内になるよう本文を短くしてください。")
        old = next((item for item in records if item["id"] == target_id), None) if target_id else None
        if target_id and not old:
            raise ValueError("置き換える知識が見つかりません。選び直してください。")
        document_id = target_id or draft_id or f"user_{uuid.uuid4().hex[:16]}.md"
        preview = {"preview_id": uuid.uuid4().hex, "created_at": _now(), "base_version": active,
                   "title": title, "content": content, "source_filename": filename or (old["source_filename"] if old else title + ".md"),
                   "target_id": document_id, "action": "replace" if old else "add", "warnings": warnings,
                   "previous_content": self.document(document_id)["content"] if old else "",
                   "original": original or (old.get("original") if old else None),
                   "origin": "USB・ファイル" if original else "画面で編集"}
        _write(self.root / "previews" / (preview["preview_id"] + ".json"), preview)
        return preview

    def _check_base(self, expected: str | None) -> None:
        if self.read_active() != expected:
            raise KnowledgeConflict("別の操作で知識が更新されました。画面を読み直し、変更内容を再確認してください。")

    def _snapshot(self, base: str | None, label: str, change: dict | None = None,
                  restore_from: str | None = None) -> tuple[str, Path]:
        version_id, created_at = uuid.uuid4().hex, _now()
        directory = self.root / "versions" / version_id
        documents_dir = directory / "documents"
        documents_dir.mkdir(parents=True)
        source_version = restore_from or base
        if source_version:
            source, manifest = self._version(source_version)
            records = [dict(item) for item in manifest["documents"]]
            source = source / "documents"
        else:
            source, records = self.seed, self._seed_documents()
        if not records and change is None:
            raise ValueError("初期知識が見つかりません。配置されたファイルを確認してください。")
        for item in records:
            _document_id(item["id"])
            _write(documents_dir / item["id"], (source / item["id"]).read_text(encoding="utf-8"))
        if change:
            record = {key: change[key] for key in ("title", "source_filename", "original", "origin")}
            record.update(id=change["target_id"], updated_at=created_at)
            records = [item for item in records if item["id"] != record["id"]] + [record]
            _write(documents_dir / record["id"], change["content"] + "\n")
        for item in records:
            if not item.get("updated_at"):
                item["updated_at"] = created_at
        _write(directory / "manifest.json", {"id": version_id, "created_at": created_at, "parent_version": base,
                                             "label": label, "documents": sorted(records, key=lambda item: item["id"])})
        return version_id, documents_dir

    def _queue(self, kind: str, expected: str | None, build: Callable) -> str:
        with self._mutex:
            fd = self._try_lock()
            if fd is None:
                raise KnowledgeConflict("別の更新を実行しています。完了してから操作してください。")
            job_id = uuid.uuid4().hex
            try:
                self._check_base(expected)
                job = {"id": job_id, "kind": kind, "status": "queued", "stage": "queued", "message": "更新の準備をしています。",
                       "progress": 0, "created_at": _now(), "updated_at": _now(), "target_version": None}
                _write(self.root / "jobs" / (job_id + ".json"), job)
                os.lseek(fd, 0, os.SEEK_SET)
                os.ftruncate(fd, 0)
                os.write(fd, json.dumps({"job_id": job_id}).encode("utf-8"))
                os.fsync(fd)
            except Exception:
                self._unlock(fd)
                raise

            def progress(stage, message, percent):
                job.update(status="running", stage=stage, message=message, progress=percent, updated_at=_now())
                _write(self.root / "jobs" / (job_id + ".json"), job)

            def run():
                try:
                    progress("preparing", "現在の知識を残しながら、新しい内容を準備しています。", 5)
                    version_id, directory = build()
                    job["target_version"] = version_id
                    progress("prepared", "資料を保存しました。検索データを準備します。", 15)
                    stats = self.indexer(directory, version_id, expected, progress)
                    job.update(status="succeeded", stage="complete", message="知識を更新できました。コモに質問して確認してください。",
                               progress=100, result={"version_id": version_id, "stats": stats}, updated_at=_now())
                except Exception as error:
                    # A crash after the DB commit is still a successful publication.
                    try:
                        committed = bool(job.get("target_version")) and self.read_active() == job["target_version"]
                    except Exception:
                        committed = False
                    if committed:
                        job.update(status="succeeded", stage="complete", progress=100,
                                   message="知識を更新できました。", result={"version_id": job["target_version"]})
                    else:
                        message = str(error) if isinstance(error, ValueError) else "更新に失敗しました。通信状態や初期設定を確認して、もう一度お試しください。現在の知識はそのまま利用できます。"
                        job.update(status="failed", stage="failed", message=message, error=message, updated_at=_now())
                        print(f"Knowledge update failed: {type(error).__name__}")
                finally:
                    try:
                        _write(self.root / "jobs" / (job_id + ".json"), job)
                    finally:
                        self._unlock(fd)
            try:
                threading.Thread(target=run, name="knowledge-update", daemon=True).start()
            except Exception:
                self._unlock(fd)
                raise
            return job_id

    def initialize(self) -> str:
        active = self.read_active()
        if active:
            job_id = uuid.uuid4().hex
            _write(self.root / "jobs" / (job_id + ".json"), {
                "id": job_id, "kind": "initialize", "status": "succeeded", "stage": "complete",
                "message": "知識の準備は完了しています。", "progress": 100,
                "created_at": _now(), "updated_at": _now(), "result": {"version_id": active},
            })
            return job_id
        return self._queue("initialize", active, lambda: self._snapshot(active, "初期知識の準備"))

    def publish(self, preview_id: str, base_version: str | None) -> str:
        preview = _read(self.root / "previews" / (_identifier(preview_id) + ".json"))
        if datetime.fromisoformat(preview["created_at"]) < datetime.now(timezone.utc) - timedelta(days=1):
            raise KnowledgeConflict("確認画面の有効期限が切れました。ファイルを選び直してください。")
        if preview["base_version"] != base_version:
            raise KnowledgeConflict("変更内容をもう一度確認してください。")
        if "【記入欄】" in preview["content"] or "【記入欄】" in preview["title"]:
            raise ValueError("Wordテンプレートの【記入欄】を記入するか、使わない項目を削除してから反映してください。確認画面でも本文を直せます。")
        return self._queue("publish", base_version, lambda: self._snapshot(base_version, f"知識を{'変更' if preview['action'] == 'replace' else '追加'}：{preview['title']}", preview))

    def restore(self, version_id: str, base_version: str | None) -> str:
        _identifier(version_id)
        if not any(item["id"] == version_id for item in self.history(self.read_active())):
            raise ValueError("指定した更新履歴が見つかりません。")
        return self._queue("restore", base_version, lambda: self._snapshot(base_version, "以前の知識に復元", restore_from=version_id))

    def job(self, job_id: str) -> dict:
        path = self.root / "jobs" / (_identifier(job_id) + ".json")
        job = _read(path)
        if job["status"] in {"queued", "running"} and not self._busy_job():
            if job.get("target_version") and self.read_active() == job["target_version"]:
                job.update(status="succeeded", stage="complete", progress=100, message="知識を更新できました。", result={"version_id": job["target_version"]})
            else:
                job.update(status="failed", stage="failed", message="更新処理が中断されました。現在の知識を確認し、もう一度お試しください。", error="更新処理が中断されました。")
            _write(path, job)
        return job


_manager = KnowledgeManager()


def get_knowledge_manager() -> KnowledgeManager:
    return _manager


def current_knowledge_directory() -> tuple[Path, str | None]:
    active = _manager.read_active()
    return ((_manager._version(active)[0] / "documents") if active else KNOWLEDGE_DIR, active)
