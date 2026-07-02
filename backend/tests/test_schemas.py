import pytest
from pydantic import ValidationError

from app.schemas import SettingsUpdateRequest, TaskCreateRequest


def test_task_container_rejects_flv():
    with pytest.raises(ValidationError):
        TaskCreateRequest(
            url="https://www.bilibili.com/video/BV1xx",
            video_format_id="video",
            merge=False,
            container="flv",
        )


def test_settings_default_container_rejects_flv():
    with pytest.raises(ValidationError):
        SettingsUpdateRequest(default_container="flv")


def test_settings_default_audio_format_rejects_unsupported_value():
    with pytest.raises(ValidationError):
        SettingsUpdateRequest(default_audio_format="wav")


def test_task_accepts_custom_filename():
    request = TaskCreateRequest(
        url="https://www.bilibili.com/video/BV1xx",
        audio_format_id="audio",
        custom_filename="我的文件名",
    )

    assert request.custom_filename == "我的文件名"
