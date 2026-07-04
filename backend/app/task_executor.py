from __future__ import annotations

import asyncio
import json
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import WebSocket

from .auth import CredentialStore
from .config import AppConfig
from .db import Database
from .extractors import BaseExtractor, ExtractorError
from .extractors.base import AuthContext
from .schemas import TaskCreateRequest, TaskResponse
from .utils import ensure_directory, now_iso


class DownloadCancelled(RuntimeError):
    pass


class TaskWebSocketHub:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = {}
        self._lock = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(self, task_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        with self._lock:
            self._connections.setdefault(task_id, set()).add(websocket)

    def disconnect(self, task_id: str, websocket: WebSocket) -> None:
        with self._lock:
            sockets = self._connections.get(task_id)
            if not sockets:
                return
            sockets.discard(websocket)
            if not sockets:
                self._connections.pop(task_id, None)

    def broadcast(self, task_id: str, payload: dict[str, Any]) -> None:
        if not self._loop:
            return
        asyncio.run_coroutine_threadsafe(self._broadcast(task_id, payload), self._loop)

    async def _broadcast(self, task_id: str, payload: dict[str, Any]) -> None:
        with self._lock:
            sockets = list(self._connections.get(task_id, set()))
        stale: list[WebSocket] = []
        for socket in sockets:
            try:
                await socket.send_json(payload)
            except Exception:
                stale.append(socket)
        for socket in stale:
            self.disconnect(task_id, socket)


class TaskExecutor:
    def __init__(
        self,
        db: Database,
        extractor: BaseExtractor,
        credential_store: CredentialStore,
        config: AppConfig,
        hub: TaskWebSocketHub,
    ) -> None:
        self.db = db
        self.extractor = extractor
        self.credential_store = credential_store
        self.config = config
        self.hub = hub
        self._stop = threading.Event()
        self._stop.set()
        self._threads: dict[int, threading.Thread] = {}
        self._worker_lock = threading.RLock()
        self._desired_workers = 1
        self._cancel_flags: dict[str, threading.Event] = {}

    def start(self) -> None:
        if any(thread.is_alive() for thread in self._threads.values()):
            return
        self.db.execute(
            "UPDATE tasks SET status = 'queued', stage = 'queued', updated_at = ? WHERE status = 'running'",
            (now_iso(),),
        )
        self._stop.clear()
        self.configure(self._configured_worker_count())

    def stop(self) -> None:
        self._stop.set()
        for thread in list(self._threads.values()):
            if thread.is_alive():
                thread.join(timeout=2)
        with self._worker_lock:
            self._threads = {slot: thread for slot, thread in self._threads.items() if thread.is_alive()}

    def configure(self, max_workers: int) -> None:
        worker_count = max(1, min(4, int(max_workers)))
        with self._worker_lock:
            self._desired_workers = worker_count
            if self._stop.is_set():
                return
            for slot in range(worker_count):
                thread = self._threads.get(slot)
                if thread and thread.is_alive():
                    continue
                thread = threading.Thread(target=self._run_worker, args=(slot,), name=f"task-executor-{slot + 1}", daemon=True)
                self._threads[slot] = thread
                thread.start()

    def _configured_worker_count(self) -> int:
        try:
            return int(self.db.get_setting("max_concurrent_downloads") or "1")
        except (TypeError, ValueError):
            return 1

    def submit(self, request: TaskCreateRequest) -> TaskResponse:
        output_dir = request.output_dir or self.db.get_setting("download_dir") or str(self.config.default_download_dir)
        output_path = ensure_directory(output_dir)
        task_id = str(uuid.uuid4())
        should_merge = bool(request.video_format_id and request.audio_format_id)
        options = {
            "video_format_id": request.video_format_id,
            "audio_format_id": request.audio_format_id,
            "audio_output_format": request.audio_output_format,
            "download_cover": request.download_cover,
            "thumbnail_url": request.thumbnail_url,
            "subtitle_track_ids": request.subtitle_track_ids,
            "subtitle_format": request.subtitle_format,
            "merge": should_merge,
            "container": request.container,
            "custom_filename": request.custom_filename.strip() if request.custom_filename else None,
        }
        timestamp = now_iso()
        self.db.execute(
            """
            INSERT INTO tasks (
                id, url, title, status, progress, stage, message, output_dir,
                options_json, created_at, updated_at
            )
            VALUES (?, ?, ?, 'queued', 0, 'queued', ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                request.url,
                request.title,
                "等待下载",
                str(output_path),
                json.dumps(options, ensure_ascii=False),
                timestamp,
                timestamp,
            ),
        )
        self._record_event(task_id, "queued", "queued", 0, "等待下载")
        return self.get(task_id)

    def list(self) -> list[TaskResponse]:
        rows = self.db.query_all("SELECT * FROM tasks ORDER BY created_at DESC LIMIT 100")
        return [self._to_response(row) for row in rows]

    def get(self, task_id: str) -> TaskResponse:
        row = self._get_row(task_id)
        return self._to_response(row)

    def cancel(self, task_id: str) -> TaskResponse:
        flag = self._cancel_flags.setdefault(task_id, threading.Event())
        flag.set()
        row = self._get_row(task_id)
        if row["status"] in {"completed", "failed", "cancelled"}:
            return self._to_response(row)
        self._update_task(task_id, status="cancelled", stage="cancelled", progress=row["progress"], message="已取消")
        return self.get(task_id)

    def retry(self, task_id: str) -> TaskResponse:
        row = self._get_row(task_id)
        if row["status"] not in {"failed", "cancelled"}:
            return self._to_response(row)
        self._cancel_flags.pop(task_id, None)
        self._update_task(task_id, status="queued", stage="queued", progress=0, message="等待重试", error=None)
        return self.get(task_id)

    def clear_finished(self) -> int:
        rows = self.db.query_all("SELECT id FROM tasks WHERE status IN ('completed', 'failed', 'cancelled')")
        task_ids = [row["id"] for row in rows]
        if not task_ids:
            return 0

        placeholders = ", ".join("?" for _ in task_ids)
        self.db.execute(f"DELETE FROM tasks WHERE id IN ({placeholders})", task_ids)
        for task_id in task_ids:
            self._cancel_flags.pop(task_id, None)
        return len(task_ids)

    def _run_worker(self, slot: int) -> None:
        try:
            while not self._stop.is_set():
                with self._worker_lock:
                    if slot >= self._desired_workers:
                        break
                row = self.db.claim_next_queued_task()
                if not row:
                    self._stop.wait(0.5)
                    continue
                task_id = row["id"]
                self._cancel_flags.setdefault(task_id, threading.Event())
                try:
                    self._execute(row)
                finally:
                    self._cancel_flags.pop(task_id, None)
        finally:
            with self._worker_lock:
                if self._threads.get(slot) is threading.current_thread():
                    self._threads.pop(slot, None)

    def _execute(self, task: dict[str, Any]) -> None:
        task_id = task["id"]
        try:
            if self._is_cancelled(task_id):
                raise DownloadCancelled("任务已取消。")
            self._update_task(task_id, status="running", stage="preparing", progress=1, message="准备下载")
            options = self._options_for_execution(task)
            cookie = None
            try:
                cookie = self.credential_store.get_bilibili_cookie()
            except Exception:
                cookie = None
            output_path = self.extractor.download(
                {**task, "options": options},
                AuthContext(cookie=cookie),
                self._progress_hook(task_id),
            )
            if self._is_cancelled(task_id):
                raise DownloadCancelled("任务已取消。")
            self._update_task(
                task_id,
                status="completed",
                stage="completed",
                progress=100,
                message="下载完成",
                output_path=output_path,
                completed_at=now_iso(),
            )
        except DownloadCancelled as exc:
            self._update_task(
                task_id,
                status="cancelled",
                stage="cancelled",
                progress=self._get_row(task_id)["progress"],
                message=str(exc),
                completed_at=now_iso(),
            )
        except ExtractorError as exc:
            self._update_task(
                task_id,
                status="failed",
                stage="failed",
                progress=self._get_row(task_id)["progress"],
                message="下载失败",
                error=str(exc),
                completed_at=now_iso(),
            )
        except Exception as exc:
            self._update_task(
                task_id,
                status="failed",
                stage="failed",
                progress=self._get_row(task_id)["progress"],
                message="下载失败",
                error=str(exc),
                completed_at=now_iso(),
            )

    def _options_for_execution(self, task: dict[str, Any]) -> dict[str, Any]:
        options = json.loads(task["options_json"])
        if options.get("video_format_id") and options.get("audio_format_id") and options.get("merge") is not True:
            options["merge"] = True
            self.db.execute(
                "UPDATE tasks SET options_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(options, ensure_ascii=False), now_iso(), task["id"]),
            )
        return options

    def _progress_hook(self, task_id: str):
        def hook(data: dict[str, Any]) -> None:
            if self._is_cancelled(task_id):
                raise DownloadCancelled("任务已取消。")
            status = data.get("status")
            if status == "downloading":
                downloaded = data.get("downloaded_bytes") or 0
                total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
                percent = min(94.0, max(2.0, downloaded / total * 90 if total else 2.0))
                speed = data.get("speed")
                eta = data.get("eta")
                message = "下载中"
                if speed:
                    message += f" · {speed / 1024 / 1024:.2f} MB/s"
                if eta:
                    message += f" · 剩余 {eta}s"
                self._update_task(task_id, status="running", stage="downloading", progress=percent, message=message)
            elif status == "finished":
                self._update_task(task_id, status="running", stage="merging", progress=95, message="下载完成，正在处理")

        return hook

    def _is_cancelled(self, task_id: str) -> bool:
        flag = self._cancel_flags.get(task_id)
        return bool(flag and flag.is_set())

    def _get_row(self, task_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if not row:
            raise KeyError(f"任务不存在：{task_id}")
        return row

    def _to_response(self, row: dict[str, Any]) -> TaskResponse:
        return TaskResponse(
            id=row["id"],
            url=row["url"],
            title=row.get("title"),
            status=row["status"],
            progress=float(row["progress"]),
            stage=row["stage"],
            message=row.get("message"),
            output_dir=row["output_dir"],
            output_path=row.get("output_path"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row.get("completed_at"),
            error=row.get("error"),
            options=json.loads(row["options_json"]),
        )

    def _update_task(self, task_id: str, **updates: Any) -> None:
        updates["updated_at"] = now_iso()
        allowed = {
            "status",
            "progress",
            "stage",
            "message",
            "output_path",
            "updated_at",
            "completed_at",
            "error",
        }
        fields = [key for key in updates if key in allowed]
        assignments = ", ".join(f"{field} = ?" for field in fields)
        params = [updates[field] for field in fields]
        params.append(task_id)
        self.db.execute(f"UPDATE tasks SET {assignments} WHERE id = ?", params)
        row = self._get_row(task_id)
        self._record_event(task_id, row["status"], row["stage"], float(row["progress"]), row.get("message"))
        self.hub.broadcast(task_id, self._to_response(row).model_dump())

    def _record_event(
        self,
        task_id: str,
        status: str,
        stage: str,
        progress: float,
        message: str | None,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO task_events (task_id, status, stage, progress, message, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (task_id, status, stage, progress, message, now_iso()),
        )
