from pathlib import Path
import json

from app.config import AppConfig
from app.db import Database
from app.extractors.base import AuthContext, BaseExtractor
from app.schemas import TaskCreateRequest
from app.task_executor import TaskExecutor, TaskWebSocketHub


class FakeCredentialStore:
    def get_bilibili_cookie(self):
        return None


class FakeExtractor(BaseExtractor):
    def supports(self, url: str) -> bool:
        return True

    def fetch_info(self, url: str, auth: AuthContext | None = None):
        return {}

    def download(self, task, auth: AuthContext | None = None, progress_hook=None):
        if progress_hook:
            progress_hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100})
            progress_hook({"status": "finished"})
        return str(Path(task["output_dir"]) / "done.mp4")


def test_task_executor_records_completed_task(tmp_path: Path):
    db = Database(tmp_path / "app.sqlite3")
    db.initialize()
    config = AppConfig(data_dir=tmp_path, database_path=tmp_path / "app.sqlite3", default_download_dir=tmp_path / "downloads")
    executor = TaskExecutor(db, FakeExtractor(), FakeCredentialStore(), config, TaskWebSocketHub())

    task = executor.submit(
        TaskCreateRequest(
            url="https://www.bilibili.com/video/BV1xx",
            title="Example",
            video_format_id="video",
            audio_format_id="audio",
            audio_output_format="m4a",
            merge=True,
            output_dir=str(tmp_path / "downloads"),
        )
    )
    row = db.query_one("SELECT * FROM tasks WHERE id = ?", (task.id,))

    executor._execute(row)

    completed = executor.get(task.id)
    assert completed.status == "completed"
    assert completed.stage == "completed"
    assert completed.progress == 100


def test_task_executor_persists_cover_options(tmp_path: Path):
    db = Database(tmp_path / "app.sqlite3")
    db.initialize()
    config = AppConfig(data_dir=tmp_path, database_path=tmp_path / "app.sqlite3", default_download_dir=tmp_path / "downloads")
    executor = TaskExecutor(db, FakeExtractor(), FakeCredentialStore(), config, TaskWebSocketHub())

    task = executor.submit(
        TaskCreateRequest(
            url="https://www.bilibili.com/video/BV1xx",
            title="Example",
            audio_format_id="audio",
            audio_output_format="mp3",
            download_cover=True,
            thumbnail_url="https://i0.hdslb.com/bfs/archive/cover.jpg",
            merge=False,
            output_dir=str(tmp_path / "downloads"),
        )
    )
    row = db.query_one("SELECT options_json FROM tasks WHERE id = ?", (task.id,))
    options = json.loads(row["options_json"])

    assert options["video_format_id"] is None
    assert options["audio_format_id"] == "audio"
    assert options["audio_output_format"] == "mp3"
    assert options["download_cover"] is True
    assert options["thumbnail_url"] == "https://i0.hdslb.com/bfs/archive/cover.jpg"


def test_task_executor_clear_finished_keeps_active_tasks(tmp_path: Path):
    db = Database(tmp_path / "app.sqlite3")
    db.initialize()
    config = AppConfig(data_dir=tmp_path, database_path=tmp_path / "app.sqlite3", default_download_dir=tmp_path / "downloads")
    executor = TaskExecutor(db, FakeExtractor(), FakeCredentialStore(), config, TaskWebSocketHub())

    completed = executor.submit(
        TaskCreateRequest(
            url="https://www.bilibili.com/video/BV1completed",
            title="Completed",
            audio_format_id="audio",
            merge=False,
            output_dir=str(tmp_path / "downloads"),
        )
    )
    queued = executor.submit(
        TaskCreateRequest(
            url="https://www.bilibili.com/video/BV1queued",
            title="Queued",
            audio_format_id="audio",
            merge=False,
            output_dir=str(tmp_path / "downloads"),
        )
    )
    executor._update_task(completed.id, status="completed", stage="completed", progress=100, message="下载完成")

    assert executor.clear_finished() == 1

    assert executor.get(queued.id).status == "queued"
    assert db.query_one("SELECT id FROM tasks WHERE id = ?", (completed.id,)) is None
    assert db.query_one("SELECT id FROM task_events WHERE task_id = ?", (completed.id,)) is None
