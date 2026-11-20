"""設置PCから操作する知識管理API。"""
from __future__ import annotations

import base64
import binascii
import secrets
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, StrictInt
from starlette.concurrency import run_in_threadpool

from chu_ai.config import CHAT_LOG_ADMIN_API_KEY
from chu_ai.services.knowledge_admin_service import KnowledgeConflict, KnowledgeJobNotFound, get_knowledge_manager
from chu_ai.services.knowledge_backup_service import MAX_BACKUP_BYTES, create_backup, restore_backup
from chu_ai.services.knowledge_import_service import MAX_UPLOAD_BYTES
from chu_ai.services.operations_service import OperationIssue, check_chat, check_connection


def require_knowledge_admin(request: Request, x_admin_key: str | None = Header(default=None)) -> None:
    try:
        hostname = request.url.hostname
        origin = request.headers.get("origin")
        origin_host = urlsplit(origin).hostname if origin else None
    except ValueError as error:
        raise HTTPException(403, "設置PCの管理画面を開き直してください。") from error
    if hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise HTTPException(403, "知識更新は設置PCのコモ管理から操作してください。")
    if origin and (origin_host not in {"localhost", "127.0.0.1", "::1"} or origin != f"{request.url.scheme}://{request.headers.get('host')}"):
        raise HTTPException(403, "設置PCの管理画面を開き直してください。")
    if not CHAT_LOG_ADMIN_API_KEY:
        raise HTTPException(503, "初回セットアップで管理用の設定を済ませてください。")
    if not x_admin_key or not secrets.compare_digest(x_admin_key.encode("utf-8"), CHAT_LOG_ADMIN_API_KEY.encode("utf-8")):
        raise HTTPException(401, "管理画面をコモ管理から開き直してください。")


router = APIRouter(prefix="/api/admin/knowledge", dependencies=[Depends(require_knowledge_admin)])


class PreviewRequest(BaseModel):
    filename: str | None = Field(default=None, max_length=255)
    content_base64: str | None = Field(default=None, max_length=((MAX_UPLOAD_BYTES + 2) // 3) * 4)
    title: str | None = Field(default=None, max_length=150)
    content: str | None = Field(default=None, max_length=200000)
    target_id: str | None = Field(default=None, max_length=200)
    source_preview_id: str | None = Field(default=None, min_length=32, max_length=32)
    import_mode: Literal["choose", "add", "replace"] | None = None


class PublishRequest(BaseModel):
    preview_id: str = Field(min_length=32, max_length=32)
    base_version: str | None = Field(default=None, max_length=32)


class RestoreRequest(BaseModel):
    version_id: str = Field(min_length=32, max_length=32)
    base_version: str | None = Field(default=None, max_length=32)


class DocumentRequest(BaseModel):
    base_version: str | None = Field(default=None, max_length=32)
    enabled: bool | None = None
    version_id: str | None = Field(default=None, min_length=32, max_length=32)


class BackupRequest(BaseModel):
    tally: dict[str, StrictInt] | None = None


def _call(method, *args, **kwargs):
    try:
        return method(*args, **kwargs)
    except KnowledgeConflict as error:
        raise HTTPException(409, str(error)) from error
    except OperationIssue as error:
        raise HTTPException(503, {"code": error.code, "message": str(error)}) from error
    except KnowledgeJobNotFound as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except Exception as error:
        print(f"Knowledge management unavailable: {type(error).__name__}")
        raise HTTPException(503, "知識の保存先に接続できません。コモ管理で起動状態と初期設定を確認してください。") from error


@router.get("/status")
def status():
    return _call(get_knowledge_manager().status)


@router.get("/documents/{document_id}")
def document(document_id: str):
    return _call(get_knowledge_manager().document, document_id)


@router.get("/readiness")
def readiness():
    return _call(get_knowledge_manager().readiness)


@router.post("/check-connection")
def connection_check():
    return _call(check_connection)


@router.post("/check-chat")
def chat_check():
    return _call(check_chat)


@router.get("/documents/{document_id}/history")
def document_history(document_id: str):
    return _call(get_knowledge_manager().document_history, document_id)


@router.post("/documents/{document_id}/change", status_code=202)
def document_change(document_id: str, payload: DocumentRequest):
    return {"job_id": _call(get_knowledge_manager().update_document, document_id, payload.base_version,
                           enabled=payload.enabled, version_id=payload.version_id)}


@router.post("/preview")
def preview(payload: PreviewRequest):
    mode = {"import_mode": payload.import_mode} if payload.import_mode else {}
    if payload.filename is not None or payload.content_base64 is not None:
        if not payload.filename or payload.content_base64 is None or payload.content is not None or payload.title is not None or payload.source_preview_id is not None:
            raise HTTPException(400, "取り込むファイルを選んでください。")
        try:
            data = base64.b64decode(payload.content_base64, validate=True)
        except (ValueError, binascii.Error) as error:
            raise HTTPException(400, "ファイルを読み取れません。選び直してください。") from error
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "8MB以内のファイルを選んでください。")
        return _call(get_knowledge_manager().preview, filename=payload.filename, data=data, target_id=payload.target_id, **mode)
    return _call(get_knowledge_manager().preview, title=payload.title, content=payload.content,
                 target_id=payload.target_id, source_preview_id=payload.source_preview_id, **mode)


@router.post("/publish", status_code=202)
def publish(payload: PublishRequest):
    return {"job_id": _call(get_knowledge_manager().publish, payload.preview_id, payload.base_version)}


@router.post("/restore", status_code=202)
def restore(payload: RestoreRequest):
    return {"job_id": _call(get_knowledge_manager().restore, payload.version_id, payload.base_version)}


@router.post("/initialize", status_code=202)
def initialize():
    return {"job_id": _call(get_knowledge_manager().initialize)}


@router.get("/jobs/{job_id}")
def job(job_id: str):
    return _call(get_knowledge_manager().job, job_id)


@router.post("/backup")
def backup(payload: BackupRequest):
    data = _call(create_backup, get_knowledge_manager(), payload.tally)
    return Response(data, media_type="application/json", headers={"Cache-Control": "no-store"})


@router.post("/backup/restore", status_code=202)
async def backup_restore(request: Request, base_version: str | None = Query(default=None, max_length=32)):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_BACKUP_BYTES:
            raise HTTPException(413, "64MB以内のバックアップを選んでください。")
        data.extend(chunk)
    job_id = await run_in_threadpool(_call, restore_backup, get_knowledge_manager(), bytes(data), base_version)
    return {"job_id": job_id}
