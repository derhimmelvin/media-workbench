from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent


@dataclass(frozen=True)
class AppConfig:
    app_name: str = "B站下载器"
    compliance_version: str = "2026-06-26-v1"
    data_dir: Path = Path(os.getenv("BILI_DOWNLOADER_DATA_DIR", BACKEND_ROOT / "data"))
    database_path: Path = Path(
        os.getenv("BILI_DOWNLOADER_DATABASE", BACKEND_ROOT / "data" / "app.sqlite3")
    )
    default_download_dir: Path = Path(
        os.getenv("BILI_DOWNLOADER_DOWNLOAD_DIR", PROJECT_ROOT / "downloads")
    )
    service_name: str = "bilibili-downloader-v1"
    bilibili_cookie_username: str = "bilibili-cookie"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.default_download_dir.mkdir(parents=True, exist_ok=True)


config = AppConfig()
