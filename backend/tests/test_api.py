from pathlib import Path

import pytest
from fastapi import HTTPException

from app import main
from app.db import Database
from app.schemas import PreviewRequest


def test_preview_requires_compliance(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db = Database(tmp_path / "app.sqlite3")
    db.initialize()
    monkeypatch.setattr(main, "db", db)

    with pytest.raises(HTTPException) as exc:
        main.preview(PreviewRequest(url="https://www.bilibili.com/video/BV1xx"))

    assert exc.value.status_code == 403
    assert "请先同意合规声明" in str(exc.value.detail)


def test_settings_include_default_audio_format(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db = Database(tmp_path / "app.sqlite3")
    db.initialize()
    db.set_setting("download_dir", str(tmp_path / "downloads"))
    db.set_setting("default_container", "mp4")
    db.set_setting("default_audio_format", "mp3")
    db.set_setting("max_concurrent_downloads", "2")
    monkeypatch.setattr(main, "db", db)

    settings = main.get_settings()

    assert settings.default_audio_format == "mp3"


def test_preview_passes_cookie_to_extractor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db = Database(tmp_path / "app.sqlite3")
    db.initialize()
    db.execute(
        "INSERT INTO compliance_consent (id, accepted, version, accepted_at) VALUES (1, 1, 'test', '2026-01-01T00:00:00+08:00')"
    )

    class FakeCredentialStore:
        def get_bilibili_cookie(self):
            return "SESSDATA=abc123; bili_jct=csrf-token"

    class FakeExtractor:
        cookie: str | None = None

        def supports(self, url: str) -> bool:
            return True

        def fetch_info(self, url: str, auth=None):
            self.cookie = auth.cookie if auth else None
            return {
                "url": url,
                "title": "Example",
                "uploader": None,
                "duration": None,
                "thumbnail": None,
                "webpage_url": url,
                "videos": [],
                "audios": [],
            }

    extractor = FakeExtractor()
    monkeypatch.setattr(main, "db", db)
    monkeypatch.setattr(main, "credential_store", FakeCredentialStore())
    monkeypatch.setattr(main, "extractor", extractor)

    main.preview(PreviewRequest(url="https://www.bilibili.com/video/BV1xx"))

    assert extractor.cookie == "SESSDATA=abc123; bili_jct=csrf-token"
