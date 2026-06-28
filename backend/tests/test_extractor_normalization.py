from app.extractors.yt_dlp_bilibili import YtDlpBilibiliExtractor
from app.media import ThumbnailPayload


def test_normalize_preview_splits_video_and_audio_formats():
    extractor = YtDlpBilibiliExtractor()
    preview = extractor._normalize_preview(
        "https://www.bilibili.com/video/BV1xx",
        {
            "title": "Example",
            "uploader": "UP",
            "duration": 125,
            "thumbnail": "https://example.test/cover.jpg",
            "formats": [
                {
                    "format_id": "video-1080",
                    "vcodec": "hev1.1.6",
                    "acodec": "none",
                    "height": 1080,
                    "width": 1920,
                    "tbr": 2400,
                    "ext": "mp4",
                },
                {
                    "format_id": "audio-30280",
                    "vcodec": "none",
                    "acodec": "mp4a.40.2",
                    "tbr": 132,
                    "ext": "m4a",
                },
            ],
        },
    )

    assert preview.title == "Example"
    assert preview.videos[0].format_id == "video-1080"
    assert preview.audios[0].format_id == "audio-30280"


def test_normalize_url_removes_tracking_parameters():
    extractor = YtDlpBilibiliExtractor()

    assert extractor.normalize_url(
        "https://www.bilibili.com/video/BV1aY4y1D7XG/?spm_id_from=333.788&trackid=abc&vd_source=secret&p=2"
    ) == "https://www.bilibili.com/video/BV1aY4y1D7XG/?p=2"


def test_options_include_browser_like_headers():
    extractor = YtDlpBilibiliExtractor()
    options = extractor._options()
    headers = options["http_headers"]

    assert headers["Accept-Language"].startswith("zh-CN")
    assert headers["Origin"] == "https://www.bilibili.com"
    assert headers["Sec-Fetch-Mode"] == "cors"
    assert options["quiet"] is True
    assert options["noprogress"] is True
    assert options["logger"] is not None


def test_friendly_412_error_message():
    extractor = YtDlpBilibiliExtractor()

    assert "412 Precondition Failed" in extractor._friendly_error(RuntimeError("HTTP Error 412: Precondition Failed"))


def test_download_cover_only_writes_thumbnail(tmp_path, monkeypatch):
    def fake_fetch_thumbnail(url: str):
        return ThumbnailPayload(content=b"cover-bytes", content_type="image/jpeg", source_url=url)

    monkeypatch.setattr("app.extractors.yt_dlp_bilibili.fetch_thumbnail", fake_fetch_thumbnail)
    output = YtDlpBilibiliExtractor().download(
        {
            "url": "https://www.bilibili.com/video/BV1xx",
            "title": "Example",
            "output_dir": str(tmp_path),
            "options": {
                "video_format_id": None,
                "audio_format_id": None,
                "download_cover": True,
                "thumbnail_url": "https://i0.hdslb.com/bfs/archive/cover.jpg",
                "merge": False,
                "container": "mp4",
            },
        }
    )

    assert output == str(tmp_path / "Example.cover.jpg")
    assert (tmp_path / "Example.cover.jpg").read_bytes() == b"cover-bytes"


def test_download_rejects_empty_resource_selection(tmp_path):
    try:
        YtDlpBilibiliExtractor().download(
            {
                "url": "https://www.bilibili.com/video/BV1xx",
                "title": "Example",
                "output_dir": str(tmp_path),
                "options": {
                    "video_format_id": None,
                    "audio_format_id": None,
                    "download_cover": False,
                    "thumbnail_url": None,
                    "merge": False,
                    "container": "mp4",
                },
            }
        )
    except Exception as exc:
        assert "请至少选择一个视频、音频或封面资源" in str(exc)
    else:
        raise AssertionError("Expected empty selection to fail.")


def test_download_rejects_video_without_audio(tmp_path):
    try:
        YtDlpBilibiliExtractor().download(
            {
                "url": "https://www.bilibili.com/video/BV1xx",
                "title": "Example",
                "output_dir": str(tmp_path),
                "options": {
                    "video_format_id": "video-1080",
                    "audio_format_id": None,
                    "download_cover": False,
                    "thumbnail_url": None,
                    "merge": False,
                    "container": "mp4",
                },
            }
        )
    except Exception as exc:
        assert "下载视频需要同时选择音频流" in str(exc)
    else:
        raise AssertionError("Expected video-only selection to fail.")


def test_download_audio_can_convert_to_mp3(tmp_path, monkeypatch):
    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def download(self, urls):
            target = self.options["outtmpl"].replace("%(ext)s", "m4a")
            with open(target, "wb") as file:
                file.write(b"source-audio")

    class FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(args, **kwargs):
        with open(args[-1], "wb") as file:
            file.write(b"mp3-audio")
        return FakeResult()

    monkeypatch.setattr(YtDlpBilibiliExtractor, "_ydl_class", lambda self: FakeYoutubeDL)
    monkeypatch.setattr("app.extractors.yt_dlp_bilibili.ffmpeg_available", lambda: True)
    monkeypatch.setattr("app.extractors.yt_dlp_bilibili.subprocess.run", fake_run)

    output = YtDlpBilibiliExtractor().download(
        {
            "url": "https://www.bilibili.com/video/BV1xx",
            "title": "Example",
            "output_dir": str(tmp_path),
            "options": {
                "video_format_id": None,
                "audio_format_id": "audio-30280",
                "audio_output_format": "mp3",
                "download_cover": False,
                "thumbnail_url": None,
                "merge": False,
                "container": "mp4",
            },
        }
    )

    assert output == str(tmp_path / "Example.audio.mp3")
    assert (tmp_path / "Example.audio.mp3").read_bytes() == b"mp3-audio"
    assert not (tmp_path / "Example.audio.m4a").exists()
