from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


TaskStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
TaskStage = Literal["queued", "preparing", "downloading", "merging", "completed", "failed", "cancelled"]
ContainerFormat = Literal["mp4", "mkv"]
AudioOutputFormat = Literal["m4a", "mp3"]


class HealthResponse(BaseModel):
    ok: bool
    database: bool
    ffmpeg: bool
    keyring: bool
    versions: dict[str, str | None]
    messages: list[str] = Field(default_factory=list)


class ComplianceStatus(BaseModel):
    accepted: bool
    version: str
    accepted_at: str | None = None
    statement: str


class ComplianceAcceptRequest(BaseModel):
    accepted: bool


class CookieStatus(BaseModel):
    configured: bool
    masked: str | None = None
    keyring_available: bool
    message: str | None = None


class CookieSaveRequest(BaseModel):
    cookie: str = Field(min_length=8, max_length=20000)


class PreviewRequest(BaseModel):
    url: str = Field(min_length=6, max_length=2000)


class MediaFormat(BaseModel):
    format_id: str
    label: str
    ext: str | None = None
    codec: str | None = None
    quality: str | None = None
    resolution: str | None = None
    bitrate: float | None = None
    fps: float | None = None
    filesize: int | None = None
    requires_auth: bool = False


class PreviewResponse(BaseModel):
    url: str
    title: str
    uploader: str | None = None
    duration: float | None = None
    thumbnail: str | None = None
    webpage_url: str | None = None
    videos: list[MediaFormat]
    audios: list[MediaFormat]


class TaskCreateRequest(BaseModel):
    url: str = Field(min_length=6, max_length=2000)
    title: str | None = None
    video_format_id: str | None = None
    audio_format_id: str | None = None
    audio_output_format: AudioOutputFormat = "m4a"
    download_cover: bool = False
    thumbnail_url: str | None = Field(default=None, max_length=2000)
    merge: bool = True
    container: ContainerFormat = "mp4"
    output_dir: str | None = None


class TaskResponse(BaseModel):
    id: str
    url: str
    title: str | None = None
    status: TaskStatus
    progress: float
    stage: str
    message: str | None = None
    output_dir: str
    output_path: str | None = None
    created_at: str
    updated_at: str
    completed_at: str | None = None
    error: str | None = None
    options: dict


class ClearTasksResponse(BaseModel):
    cleared: int


class OpenFolderResponse(BaseModel):
    opened: bool
    path: str


class SettingsResponse(BaseModel):
    download_dir: str
    default_container: ContainerFormat
    max_concurrent_downloads: int


class SettingsUpdateRequest(BaseModel):
    download_dir: str | None = None
    default_container: ContainerFormat | None = None
    max_concurrent_downloads: int | None = Field(default=None, ge=1, le=4)
