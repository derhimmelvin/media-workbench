from __future__ import annotations

import asyncio
import sqlite3
from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from .auth import CredentialStore, CredentialStoreError
from .config import config
from .db import Database
from .extractors import ExtractorError, YtDlpBilibiliExtractor
from .extractors.base import AuthContext
from .media import MediaProxyError, fetch_thumbnail
from .schemas import (
    ClearTasksResponse,
    ComplianceAcceptRequest,
    ComplianceStatus,
    CookieSaveRequest,
    CookieStatus,
    AudioOutputFormat,
    ContainerFormat,
    HealthResponse,
    PreviewRequest,
    PreviewResponse,
    OpenFolderResponse,
    SettingsResponse,
    SettingsUpdateRequest,
    TaskCreateRequest,
    TaskResponse,
)
from .task_executor import TaskExecutor, TaskWebSocketHub
from .utils import (
    ensure_directory,
    ffmpeg_available,
    open_directory,
    resolve_task_output_directory,
    runtime_versions,
)


COMPLIANCE_STATEMENT = (
    "本工具仅供学习、研究和个人合理使用，严禁用于侵犯版权或商业传播，"
    "用户须自行承担法律责任。"
)

db = Database(config.database_path)
credential_store = CredentialStore(db, config)
extractor = YtDlpBilibiliExtractor()
hub = TaskWebSocketHub()
executor = TaskExecutor(db, extractor, credential_store, config, hub)
ALLOWED_CONTAINERS = {"mp4", "mkv"}
ALLOWED_AUDIO_OUTPUT_FORMATS = {"m4a", "mp3"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()
    db.initialize()
    _ensure_default_settings()
    hub.set_loop(asyncio.get_running_loop())
    executor.start()
    yield
    executor.stop()


app = FastAPI(title="B站下载器 API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _ensure_default_settings() -> None:
    if not db.get_setting("download_dir"):
        db.set_setting("download_dir", str(config.default_download_dir))
    stored_container = db.get_setting("default_container")
    normalized_container = _normalize_container(stored_container)
    if stored_container != normalized_container:
        db.set_setting("default_container", normalized_container)
    stored_audio_format = db.get_setting("default_audio_format")
    normalized_audio_format = _normalize_audio_format(stored_audio_format)
    if stored_audio_format != normalized_audio_format:
        db.set_setting("default_audio_format", normalized_audio_format)
    if not db.get_setting("max_concurrent_downloads"):
        db.set_setting("max_concurrent_downloads", "1")


def _normalize_container(value: str | None) -> ContainerFormat:
    if value in ALLOWED_CONTAINERS:
        return cast(ContainerFormat, value)
    return "mp4"


def _normalize_audio_format(value: str | None) -> AudioOutputFormat:
    if value in ALLOWED_AUDIO_OUTPUT_FORMATS:
        return cast(AudioOutputFormat, value)
    return "m4a"


def _task_or_404(task_id: str) -> TaskResponse:
    try:
        return executor.get(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="任务不存在。") from exc


def _require_compliance() -> None:
    row = db.query_one("SELECT accepted FROM compliance_consent WHERE id = 1")
    if not row or not row["accepted"]:
        raise HTTPException(status_code=403, detail="请先同意合规声明。")


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    messages: list[str] = []
    database_ok = True
    try:
        db.query_one("SELECT 1 AS ok")
    except sqlite3.Error:
        database_ok = False
        messages.append("SQLite 数据库不可用。")
    versions = runtime_versions()
    if not versions.get("yt_dlp"):
        messages.append("yt-dlp 未安装，无法解析和下载。")
    if not ffmpeg_available():
        messages.append("FFmpeg 未安装，无法合并音视频。")
    keyring_ok = credential_store.is_available()
    if not keyring_ok:
        messages.append("系统钥匙串不可用，Cookie 无法持久化。")
    return HealthResponse(
        ok=database_ok and bool(versions.get("yt_dlp")) and keyring_ok,
        database=database_ok,
        ffmpeg=ffmpeg_available(),
        keyring=keyring_ok,
        versions=versions,
        messages=messages,
    )


@app.get("/api/compliance", response_model=ComplianceStatus)
def get_compliance() -> ComplianceStatus:
    row = db.query_one("SELECT * FROM compliance_consent WHERE id = 1")
    return ComplianceStatus(
        accepted=bool(row and row["accepted"]),
        version=row["version"] if row else config.compliance_version,
        accepted_at=row["accepted_at"] if row else None,
        statement=COMPLIANCE_STATEMENT,
    )


@app.post("/api/compliance", response_model=ComplianceStatus)
def accept_compliance(request: ComplianceAcceptRequest) -> ComplianceStatus:
    from .utils import now_iso

    accepted_at = now_iso() if request.accepted else None
    db.execute(
        """
        INSERT INTO compliance_consent (id, accepted, version, accepted_at)
        VALUES (1, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            accepted = excluded.accepted,
            version = excluded.version,
            accepted_at = excluded.accepted_at
        """,
        (1 if request.accepted else 0, config.compliance_version, accepted_at),
    )
    return get_compliance()


@app.get("/api/auth/bilibili-cookie", response_model=CookieStatus)
def get_cookie_status() -> CookieStatus:
    state = credential_store.status()
    return CookieStatus(**state.__dict__)


@app.post("/api/auth/bilibili-cookie", response_model=CookieStatus)
def save_cookie(request: CookieSaveRequest) -> CookieStatus:
    try:
        state = credential_store.save_bilibili_cookie(request.cookie)
    except CredentialStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return CookieStatus(**state.__dict__)


@app.delete("/api/auth/bilibili-cookie", response_model=CookieStatus)
def delete_cookie() -> CookieStatus:
    try:
        state = credential_store.delete_bilibili_cookie()
    except CredentialStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return CookieStatus(**state.__dict__)


@app.get("/api/settings", response_model=SettingsResponse)
def get_settings() -> SettingsResponse:
    return SettingsResponse(
        download_dir=db.get_setting("download_dir") or str(config.default_download_dir),
        default_container=_normalize_container(db.get_setting("default_container")),
        default_audio_format=_normalize_audio_format(db.get_setting("default_audio_format")),
        max_concurrent_downloads=int(db.get_setting("max_concurrent_downloads") or "1"),
    )


@app.post("/api/settings", response_model=SettingsResponse)
def update_settings(request: SettingsUpdateRequest) -> SettingsResponse:
    if request.download_dir is not None:
        db.set_setting("download_dir", str(ensure_directory(request.download_dir)))
    if request.default_container is not None:
        db.set_setting("default_container", request.default_container)
    if request.default_audio_format is not None:
        db.set_setting("default_audio_format", request.default_audio_format)
    if request.max_concurrent_downloads is not None:
        db.set_setting("max_concurrent_downloads", str(request.max_concurrent_downloads))
    settings = get_settings()
    executor.configure(settings.max_concurrent_downloads)
    return settings


@app.post("/api/preview", response_model=PreviewResponse)
def preview(request: PreviewRequest) -> PreviewResponse:
    _require_compliance()
    if not extractor.supports(request.url):
        raise HTTPException(status_code=400, detail="当前仅支持 B站链接、BV号、av号、ep号或 ss号。")
    try:
        cookie = credential_store.get_bilibili_cookie()
    except Exception:
        cookie = None
    try:
        data = extractor.fetch_info(request.url, AuthContext(cookie=cookie))
    except ExtractorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PreviewResponse(**data)


@app.get("/api/media/thumbnail")
def thumbnail(url: str) -> Response:
    try:
        payload = fetch_thumbnail(url)
    except MediaProxyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return Response(
        content=payload.content,
        media_type=payload.content_type,
        headers={
            "Cache-Control": "public, max-age=86400",
            "X-Thumbnail-Source": payload.source_url,
        },
    )


@app.post("/api/tasks", response_model=TaskResponse)
def create_task(request: TaskCreateRequest) -> TaskResponse:
    _require_compliance()
    if not extractor.supports(request.url):
        raise HTTPException(status_code=400, detail="当前仅支持 B站链接、BV号、av号、ep号或 ss号。")
    if not request.video_format_id and not request.audio_format_id and not request.download_cover:
        raise HTTPException(status_code=400, detail="请至少选择一个视频、音频或封面资源。")
    if request.video_format_id and not request.audio_format_id:
        raise HTTPException(status_code=400, detail="下载视频需要同时选择音频流，否则文件会没有声音。")
    if request.download_cover and not request.thumbnail_url:
        raise HTTPException(status_code=400, detail="当前资源没有可下载的封面。")
    return executor.submit(request)


@app.get("/api/tasks", response_model=list[TaskResponse])
def list_tasks() -> list[TaskResponse]:
    return executor.list()


@app.delete("/api/tasks", response_model=ClearTasksResponse)
def clear_tasks() -> ClearTasksResponse:
    return ClearTasksResponse(cleared=executor.clear_finished())


@app.get("/api/tasks/{task_id}", response_model=TaskResponse)
def get_task(task_id: str) -> TaskResponse:
    return _task_or_404(task_id)


@app.post("/api/tasks/{task_id}/cancel", response_model=TaskResponse)
def cancel_task(task_id: str) -> TaskResponse:
    _task_or_404(task_id)
    return executor.cancel(task_id)


@app.post("/api/tasks/{task_id}/retry", response_model=TaskResponse)
def retry_task(task_id: str) -> TaskResponse:
    _task_or_404(task_id)
    return executor.retry(task_id)


@app.post("/api/tasks/{task_id}/open-folder", response_model=OpenFolderResponse)
def open_task_folder(task_id: str) -> OpenFolderResponse:
    task = _task_or_404(task_id)
    if task.status != "completed":
        raise HTTPException(status_code=400, detail="只有下载完成的任务才能打开目录。")
    try:
        target_dir = resolve_task_output_directory(task.output_path, task.output_dir)
        open_directory(target_dir)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail=f"无法打开本地目录：{exc}") from exc
    return OpenFolderResponse(opened=True, path=str(target_dir))


@app.websocket("/ws/tasks/{task_id}")
async def task_socket(websocket: WebSocket, task_id: str) -> None:
    await hub.connect(task_id, websocket)
    try:
        try:
            await websocket.send_json(executor.get(task_id).model_dump())
        except KeyError:
            await websocket.send_json({"id": task_id, "status": "failed", "message": "任务不存在。"})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        hub.disconnect(task_id, websocket)
