from pathlib import Path
import json
import threading
import time

from app.config import AppConfig
from app.db import Database
from app.extractors.base import AuthContext, BaseExtractor
from app.schemas import TaskCreateRequest
from app.task_executor import TaskExecutor, TaskWebSocketHub


class FakeCredentialStore:
    def get_bilibili_cookie(self):
        return None


class FakeExtractor(BaseExtractor):
    def __init__(self) -> None:
        self.downloaded_tasks: list[dict] = []

    def supports(self, url: str) -> bool:
        return True

    def fetch_info(self, url: str, auth: AuthContext | None = None):
        return {}

    def download(self, task, auth: AuthContext | None = None, progress_hook=None):
        self.downloaded_tasks.append(task)
        if progress_hook:
            progress_hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100})
            progress_hook({"status": "finished"})
        return str(Path(task["output_dir"]) / "done.mp4")


class BlockingExtractor(FakeExtractor):
    def __init__(self) -> None:
        self.release = threading.Event()
        self._lock = threading.RLock()
        self.started: list[str] = []

    def download(self, task, auth: AuthContext | None = None, progress_hook=None):
        with self._lock:
            self.started.append(task["id"])
        if progress_hook:
            progress_hook({"status": "downloading", "downloaded_bytes": 10, "total_bytes": 100})
        self.release.wait(timeout=5)
        if progress_hook:
            progress_hook({"status": "finished"})
        return str(Path(task["output_dir"]) / f"{task['id']}.mp4")

    def wait_for_started(self, count: int, timeout: float = 3) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if len(self.started) >= count:
                    return True
            time.sleep(0.02)
        return False


def wait_until(predicate, timeout: float = 3) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


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


def test_task_executor_persists_custom_filename_option(tmp_path: Path):
    db = Database(tmp_path / "app.sqlite3")
    db.initialize()
    config = AppConfig(data_dir=tmp_path, database_path=tmp_path / "app.sqlite3", default_download_dir=tmp_path / "downloads")
    executor = TaskExecutor(db, FakeExtractor(), FakeCredentialStore(), config, TaskWebSocketHub())

    task = executor.submit(
        TaskCreateRequest(
            url="https://www.bilibili.com/video/BV1xx",
            title="Example",
            audio_format_id="audio",
            merge=False,
            output_dir=str(tmp_path / "downloads"),
            custom_filename="自定义名称",
        )
    )
    row = db.query_one("SELECT options_json FROM tasks WHERE id = ?", (task.id,))
    options = json.loads(row["options_json"])

    assert options["custom_filename"] == "自定义名称"


def test_task_executor_persists_subtitle_options(tmp_path: Path):
    db = Database(tmp_path / "app.sqlite3")
    db.initialize()
    config = AppConfig(data_dir=tmp_path, database_path=tmp_path / "app.sqlite3", default_download_dir=tmp_path / "downloads")
    executor = TaskExecutor(db, FakeExtractor(), FakeCredentialStore(), config, TaskWebSocketHub())

    task = executor.submit(
        TaskCreateRequest(
            url="https://www.bilibili.com/video/BV1xx",
            title="Example",
            subtitle_track_ids=["normal:zh-Hans"],
            subtitle_format="txt",
            merge=False,
            output_dir=str(tmp_path / "downloads"),
        )
    )
    row = db.query_one("SELECT options_json FROM tasks WHERE id = ?", (task.id,))
    options = json.loads(row["options_json"])

    assert options["subtitle_track_ids"] == ["normal:zh-Hans"]
    assert options["subtitle_format"] == "txt"


def test_task_executor_persists_thumbnail_for_task_list_when_cover_not_downloaded(tmp_path: Path):
    db = Database(tmp_path / "app.sqlite3")
    db.initialize()
    config = AppConfig(data_dir=tmp_path, database_path=tmp_path / "app.sqlite3", default_download_dir=tmp_path / "downloads")
    executor = TaskExecutor(db, FakeExtractor(), FakeCredentialStore(), config, TaskWebSocketHub())

    task = executor.submit(
        TaskCreateRequest(
            url="https://www.bilibili.com/video/BV1xx",
            title="Example",
            audio_format_id="audio",
            download_cover=False,
            thumbnail_url="https://i0.hdslb.com/bfs/archive/cover.jpg",
            merge=False,
            output_dir=str(tmp_path / "downloads"),
        )
    )

    assert task.options["download_cover"] is False
    assert task.options["thumbnail_url"] == "https://i0.hdslb.com/bfs/archive/cover.jpg"


def test_task_executor_forces_merge_for_video_tasks(tmp_path: Path):
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
            merge=False,
            output_dir=str(tmp_path / "downloads"),
        )
    )
    row = db.query_one("SELECT options_json FROM tasks WHERE id = ?", (task.id,))
    options = json.loads(row["options_json"])

    assert options["merge"] is True


def test_task_executor_normalizes_legacy_video_task_before_download(tmp_path: Path):
    db = Database(tmp_path / "app.sqlite3")
    db.initialize()
    config = AppConfig(data_dir=tmp_path, database_path=tmp_path / "app.sqlite3", default_download_dir=tmp_path / "downloads")
    extractor = FakeExtractor()
    executor = TaskExecutor(db, extractor, FakeCredentialStore(), config, TaskWebSocketHub())
    options = {
        "video_format_id": "video",
        "audio_format_id": "audio",
        "audio_output_format": "m4a",
        "download_cover": False,
        "thumbnail_url": None,
        "merge": False,
        "container": "mp4",
        "custom_filename": None,
    }
    db.execute(
        """
        INSERT INTO tasks (
            id, url, title, status, progress, stage, message, output_dir,
            options_json, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "legacy-video",
            "https://www.bilibili.com/video/BV1legacy",
            "Legacy",
            "queued",
            0,
            "queued",
            "等待下载",
            str(tmp_path / "downloads"),
            json.dumps(options, ensure_ascii=False),
            "2026-01-01T00:00:00+08:00",
            "2026-01-01T00:00:00+08:00",
        ),
    )
    row = db.query_one("SELECT * FROM tasks WHERE id = ?", ("legacy-video",))

    executor._execute(row)

    assert extractor.downloaded_tasks[0]["options"]["merge"] is True
    stored = db.query_one("SELECT options_json FROM tasks WHERE id = ?", ("legacy-video",))
    assert json.loads(stored["options_json"])["merge"] is True


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


def test_task_executor_runs_up_to_configured_concurrency(tmp_path: Path):
    db = Database(tmp_path / "app.sqlite3")
    db.initialize()
    db.set_setting("max_concurrent_downloads", "2")
    config = AppConfig(data_dir=tmp_path, database_path=tmp_path / "app.sqlite3", default_download_dir=tmp_path / "downloads")
    extractor = BlockingExtractor()
    executor = TaskExecutor(db, extractor, FakeCredentialStore(), config, TaskWebSocketHub())
    executor.start()
    try:
        tasks = [
            executor.submit(
                TaskCreateRequest(
                    url=f"https://www.bilibili.com/video/BV1x{index}",
                    title=f"Task {index}",
                    audio_format_id="audio",
                    merge=False,
                    output_dir=str(tmp_path / "downloads"),
                )
            )
            for index in range(3)
        ]

        assert extractor.wait_for_started(2)
        statuses = [executor.get(task.id).status for task in tasks]
        assert statuses.count("running") == 2
        assert statuses.count("queued") == 1

        extractor.release.set()
        assert wait_until(lambda: all(executor.get(task.id).status == "completed" for task in tasks))
        with extractor._lock:
            assert len(set(extractor.started)) == 3
    finally:
        extractor.release.set()
        executor.stop()
