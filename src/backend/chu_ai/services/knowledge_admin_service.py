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
from chu_ai.services.operations_service import diagnose


class KnowledgeConflict(ValueError):
    """再確認が必要な競合・実行中の操作。"""


class KnowledgeJobNotFound(ValueError):
    """すでに存在しないジョブ。"""


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
    if (not isinstance(value, str) or not value or len(value) > 200
            or value.startswith(".") or re.search(r'[\x00-\x1f<>:"/\\|?*]', value)
            or not value.endswith(".md") or value == KNOWLEDGE_INDEX_FILE):
        raise ValueError("選んだ案内が見つかりません。")
    return value


def _record_path(directory: Path, record: dict) -> Path:
    folder = "documents" if record.get("enabled", True) else "inactive"
    return directory / folder / _document_id(record["id"])


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
    progress("database", "コモがいま使っている案内を確かめています。", 18)
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
        progress("embedding", "コモが回答に使えるよう、新しい案内を準備しています。", 20 + int(start / max(1, len(payloads)) * 65))
        vectors = embed_texts([text for _, text in batch])
        if len(vectors) != len(batch):
            raise RuntimeError("Incomplete embeddings")
        embeddings.update((key, vector) for (key, _), vector in zip(batch, vectors))
    progress("publishing", "新しい案内に切り替えています。", 90)
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

    def history(self, current: str | None, limit: int = 128) -> list[dict]:
        result, seen = [], set()
        cursor = current
        while cursor and cursor not in seen and len(result) < limit:
            seen.add(cursor)
            _, manifest = self._version(cursor)
            result.append({"id": cursor, "created_at": manifest["created_at"],
                           "label": manifest["label"], "document_count": len(manifest["documents"]),
                           "current": cursor == current})
            cursor = manifest.get("parent_version")
        return result

    def readiness(self) -> dict:
        active = self.read_active()
        manifest = self._version(active)[1] if active else None
        job = self._busy_job()
        try:
            last = self.job(_read(self.root / "last_job.json")["id"])
        except (ValueError, KeyError):
            last = None
        return {"initialized": bool(active), "initialization_required": not bool(active),
                "active_version": active, "last_updated": manifest["created_at"] if manifest else None,
                "busy": bool(job), "active_job": job if job != "starting" else None,
                "last_job": last}

    def status(self) -> dict:
        summary = self.readiness()
        active = summary["active_version"]
        manifest = self._version(active)[1] if active else None
        return {**summary,
                "documents": manifest["documents"] if manifest else self._seed_documents(),
                "history": self.history(active)}

    def document(self, document_id: str) -> dict:
        _document_id(document_id)
        active = self.read_active()
        if active:
            directory, manifest = self._version(active)
            records = manifest["documents"]
        else:
            records = self._seed_documents()
        record = next((item for item in records if item["id"] == document_id), None)
        if not record:
            raise ValueError("選んだ案内が見つかりません。画面を読み直してください。")
        path = _record_path(directory, record) if active else self.seed / document_id
        return {**record, "content": path.read_text(encoding="utf-8")}

    def save_original(self, name: str, data: bytes) -> None:
        destination = self.root / "originals" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            return
        path = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as out:
                path = Path(out.name)
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            os.replace(path, destination)
        finally:
            if path is not None:
                path.unlink(missing_ok=True)

    def preview(self, *, filename: str | None = None, data: bytes | None = None,
                title: str | None = None, content: str | None = None,
                target_id: str | None = None, source_preview_id: str | None = None,
                import_mode: str | None = None) -> dict:
        from chu_ai.services.knowledge_import_service import import_knowledge_file
        active = self.read_active()
        if not active:
            raise KnowledgeConflict("最初に「準備を始める」を押してください。")
        directory, manifest = self._version(active)
        records = manifest["documents"]
        warnings = []
        original = None
        draft_id = None
        inherited = None
        suggestions = []
        if source_preview_id:
            inherited = _read(self.root / "previews" / (_identifier(source_preview_id) + ".json"))
            if inherited["base_version"] != active:
                raise KnowledgeConflict("ほかの操作で案内が変わりました。ファイルを選び直し、内容を確かめてください。")
            if datetime.fromisoformat(inherited["created_at"]) < datetime.now(timezone.utc) - timedelta(days=1):
                raise KnowledgeConflict("確認画面の有効期限が切れました。ファイルを選び直してください。")
            original = inherited.get("original")
            warnings = inherited.get("warnings", [])
            if inherited["action"] == "replace" and import_mode not in {"add", "choose"}:
                target_id = target_id or inherited["target_id"]
            elif inherited["action"] == "add":
                draft_id = inherited["target_id"]
            suggestions = inherited.get("suggested_targets", [])
        if filename is not None and data is not None:
            imported = import_knowledge_file(filename, data)
            filename, title, content = imported["source_filename"], imported["title"], imported["content"]
            warnings = imported["warnings"]
            if len(title) > 150:
                title = title[:150]
                warnings = [*warnings, "タイトルを150文字に短縮しました。内容を確認してください。"]
            original = hashlib.sha256(data).hexdigest() + Path(filename).suffix.lower()
            self.save_original(original, data)
            matches = [item for item in records if Path(item["source_filename"]).stem.casefold() == Path(filename).stem.casefold()]
            suggestions = [{"id": item["id"], "title": item["title"]} for item in matches]
            # Preserve the older preview API for existing management windows.
            if import_mode is None and not target_id and len(matches) == 1:
                target_id = matches[0]["id"]
                warnings = [*warnings, "同じ名前の案内があります。入れ替える内容を確かめてください。"]
        else:
            filename = inherited.get("source_filename") if inherited else None
        if import_mode == "replace" and not target_id:
            raise ValueError("入れ替える案内を選んでください。")
        if import_mode in {"add", "choose"}:
            target_id = None
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
            raise ValueError("入れ替える案内が見つかりません。選び直してください。")
        document_id = target_id or draft_id or f"user_{uuid.uuid4().hex[:16]}.md"
        previous_content = _record_path(directory, old).read_text(encoding="utf-8") if old else ""
        preview = {"preview_id": uuid.uuid4().hex, "created_at": _now(), "base_version": active,
                   "title": title, "content": content, "source_filename": filename or (old["source_filename"] if old else title + ".md"),
                   "target_id": document_id, "action": "replace" if old else "add", "warnings": warnings,
                   "previous_content": previous_content, "previous_title": old["title"] if old else "",
                   "suggested_targets": suggestions, "needs_target_confirmation": import_mode == "choose" and bool(suggestions),
                   "original": original or (old.get("original") if old else None),
                   "origin": "USB・ファイル" if original else "画面で編集",
                   "enabled": old.get("enabled", True) if old else True}
        _write(self.root / "previews" / (preview["preview_id"] + ".json"), preview)
        return preview

    def _check_base(self, expected: str | None) -> None:
        if self.read_active() != expected:
            raise KnowledgeConflict("別の操作で知識が更新されました。画面を読み直し、変更内容を再確認してください。")

    def _make_version(self, parent: str | None, label: str, items: list[tuple[dict, str]],
                      created_at: str | None = None) -> tuple[str, Path]:
        if not any(record.get("enabled", True) for record, _ in items):
            raise ValueError("利用できる資料がありません。少なくとも1件の資料を利用中にしてください。")
        version_id, created_at = uuid.uuid4().hex, created_at or _now()
        directory = self.root / "versions" / version_id
        documents_dir = directory / "documents"
        documents_dir.mkdir(parents=True)
        records = []
        for item, content in items:
            record = dict(item)
            if not record.get("updated_at"):
                record["updated_at"] = created_at
            _write(_record_path(directory, record), content)
            records.append(record)
        _write(directory / "manifest.json", {"id": version_id, "created_at": created_at, "parent_version": parent,
                                             "label": label, "documents": sorted(records, key=lambda item: item["id"])})
        return version_id, documents_dir

    def _snapshot(self, base: str | None, label: str, change: dict | None = None,
                  restore_from: str | None = None) -> tuple[str, Path]:
        source_version = restore_from or base
        if source_version:
            source, manifest = self._version(source_version)
            items = [(item, _record_path(source, item).read_text(encoding="utf-8"))
                     for item in manifest["documents"]]
        else:
            items = [(item, (self.seed / _document_id(item["id"])).read_text(encoding="utf-8"))
                     for item in self._seed_documents()]
        if not items and change is None:
            raise ValueError("最初の案内が見つかりません。設置を担当した人に記録を渡してください。")
        if change:
            record = {key: change[key] for key in ("title", "source_filename", "original", "origin")}
            record.update(id=change["target_id"], enabled=change.get("enabled", True))
            items = [(item, text) for item, text in items if item["id"] != record["id"]]
            items.append((record, change["content"].rstrip() + "\n"))
        return self._make_version(base, label, items)

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
                _write(self.root / "last_job.json", {"id": job_id})
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
                    progress("preparing", "今の案内を残しながら、新しい内容を用意しています。", 5)
                    version_id, directory = build()
                    job["target_version"] = version_id
                    progress("prepared", "案内を保存しました。コモの回答に使えるよう準備しています。", 15)
                    stats = self.indexer(directory, version_id, expected, progress)
                    job.update(status="succeeded", stage="complete", message="案内を変えました。コモに聞いて確かめてください。",
                               progress=100, result={"version_id": version_id, "stats": stats}, updated_at=_now())
                except Exception as error:
                    # A crash after the DB commit is still a successful publication.
                    try:
                        committed = bool(job.get("target_version")) and self.read_active() == job["target_version"]
                    except Exception:
                        committed = False
                    if committed:
                        job.update(status="succeeded", stage="complete", progress=100,
                                   message="案内を変えました。", result={"version_id": job["target_version"]})
                    else:
                        issue = diagnose(error, job["stage"])
                        message = str(error) if type(error) is ValueError and job["stage"] == "preparing" else str(issue)
                        if expected:
                            message += "コモは今までの案内を使えます。"
                        job.update(status="failed", stage="failed", message=message, error=message,
                                   error_code=issue.code, updated_at=_now())
                        print(f"Knowledge update failed: {issue.code} ({type(error).__name__})")
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
                "message": "最初の案内は準備できています。", "progress": 100,
                "created_at": _now(), "updated_at": _now(), "result": {"version_id": active},
            })
            return job_id
        return self._queue("initialize", active, lambda: self._snapshot(active, "最初の案内の準備"))

    def publish(self, preview_id: str, base_version: str | None) -> str:
        preview = _read(self.root / "previews" / (_identifier(preview_id) + ".json"))
        if datetime.fromisoformat(preview["created_at"]) < datetime.now(timezone.utc) - timedelta(days=1):
            raise KnowledgeConflict("確認画面の有効期限が切れました。ファイルを選び直してください。")
        if preview["base_version"] != base_version:
            raise KnowledgeConflict("変更内容をもう一度確認してください。")
        if preview.get("needs_target_confirmation"):
            raise ValueError("別の案内として追加するか、今の案内を入れ替えるか選んでください。")
        if "【記入欄】" in preview["content"] or "【記入欄】" in preview["title"]:
            raise ValueError("Wordのひな形の【記入欄】に案内を書くか、使わない項目を消してください。この画面でも書き直せます。")
        return self._queue("publish", base_version, lambda: self._snapshot(base_version, f"案内を{'変更' if preview['action'] == 'replace' else '追加'}：{preview['title']}", preview))

    def restore(self, version_id: str, base_version: str | None) -> str:
        _identifier(version_id)
        if not any(item["id"] == version_id for item in self.history(self.read_active())):
            raise ValueError("指定した更新履歴が見つかりません。")
        return self._queue("restore", base_version, lambda: self._snapshot(base_version, "以前の案内に戻す", restore_from=version_id))

    def document_history(self, document_id: str) -> list[dict]:
        _document_id(document_id)
        versions = []
        previous = None
        for entry in self.history(self.read_active()):
            directory, manifest = self._version(entry["id"])
            item = next((item for item in manifest["documents"] if item["id"] == document_id), None)
            if not item:
                continue
            content = _record_path(directory, item).read_text(encoding="utf-8")
            signature = (item["title"], item.get("enabled", True), content)
            if signature != previous:
                versions.append({**entry, "title": item["title"], "enabled": signature[1]})
                previous = signature
        return versions

    def update_document(self, document_id: str, base_version: str | None, *,
                        enabled: bool | None = None, version_id: str | None = None) -> str:
        _document_id(document_id)
        self._check_base(base_version)
        if not base_version or (enabled is None) == (version_id is None):
            raise ValueError("資料の操作を選び直してください。")
        current = self.document(document_id)
        if version_id:
            if not any(item["id"] == version_id for item in self.document_history(document_id)):
                raise ValueError("選んだ資料の履歴が見つかりません。")
            directory, manifest = self._version(version_id)
            record = next(item for item in manifest["documents"] if item["id"] == document_id)
            change = {**record, "content": _record_path(directory, record).read_text(encoding="utf-8")}
            label = "ひとつの案内を元に戻す"
        else:
            if enabled == current.get("enabled", True):
                raise ValueError("資料はすでにこの状態です。")
            change = {**current, "enabled": enabled}
            label = "案内をまた使う" if enabled else "案内の利用を止める"
        change.update(target_id=document_id, original=change.get("original"))
        return self._queue("document", base_version,
                           lambda: self._snapshot(base_version, f"{label}：{change['title']}", change))

    def job(self, job_id: str) -> dict:
        path = self.root / "jobs" / (_identifier(job_id) + ".json")
        if not path.is_file():
            raise KnowledgeJobNotFound("この操作の記録が見つかりません。")
        job = _read(path)
        if job["status"] in {"queued", "running"} and not self._busy_job():
            if job.get("target_version") and self.read_active() == job["target_version"]:
                job.update(status="succeeded", stage="complete", progress=100, message="案内を変えました。", result={"version_id": job["target_version"]})
            else:
                job.update(status="failed", stage="failed", message="途中で止まりました。案内が変わったか確かめてから、もう一度試してください。", error="途中で止まりました。", error_code="interrupted")
            _write(path, job)
        return job


_manager = KnowledgeManager()


def get_knowledge_manager() -> KnowledgeManager:
    return _manager


def current_knowledge_directory() -> tuple[Path, str | None]:
    active = _manager.read_active()
    return ((_manager._version(active)[0] / "documents") if active else KNOWLEDGE_DIR, active)
