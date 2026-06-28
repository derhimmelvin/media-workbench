from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .base import AuthContext, BaseExtractor, ExtractorError
from ..media import MediaProxyError, ThumbnailPayload, fetch_thumbnail
from ..schemas import MediaFormat, PreviewResponse
from ..utils import ffmpeg_available, sanitize_filename


BILIBILI_PATTERN = re.compile(
    r"(bilibili\.com|b23\.tv|^BV[0-9A-Za-z]+$|^av\d+$|^ep\d+$|^ss\d+$)",
    re.IGNORECASE,
)
SAFE_BILIBILI_QUERY_KEYS = {"p", "page", "bvid", "aid", "cid", "ep_id", "season_id"}


class _YtDlpLogger:
    def debug(self, message: str) -> None:
        return None

    def warning(self, message: str) -> None:
        return None

    def error(self, message: str) -> None:
        return None


def _thumbnail_extension(payload: ThumbnailPayload) -> str:
    extensions = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "image/gif": "gif",
    }
    return extensions.get(payload.content_type, "jpg")


def _find_downloaded_file(output_dir: Path, stem: str, before: set[Path] | None = None) -> Path:
    before = before or set()
    candidates = [
        path
        for path in output_dir.iterdir()
        if path.is_file() and path.name.startswith(f"{stem}.") and not path.name.endswith(".part")
    ]
    new_candidates = [path for path in candidates if path not in before]
    candidates = new_candidates or candidates
    if not candidates:
        raise ExtractorError(f"未找到下载产物：{stem}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _convert_audio_to_mp3(source_path: Path, target_path: Path) -> Path:
    if not ffmpeg_available():
        raise ExtractorError("未检测到 FFmpeg，无法转换 MP3。")

    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source_path),
            "-vn",
            "-codec:a",
            "libmp3lame",
            "-q:a",
            "2",
            str(target_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise ExtractorError(f"MP3 转换失败：{detail or 'FFmpeg 执行失败。'}")
    return target_path


class YtDlpBilibiliExtractor(BaseExtractor):
    def supports(self, url: str) -> bool:
        return bool(BILIBILI_PATTERN.search(url.strip()))

    def normalize_url(self, url: str) -> str:
        stripped = url.strip()
        if re.match(r"^(BV[0-9A-Za-z]+|av\d+|ep\d+|ss\d+)$", stripped, re.IGNORECASE):
            return stripped

        parsed = urlparse(stripped)
        if not parsed.netloc.endswith("bilibili.com"):
            return stripped

        query = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key in SAFE_BILIBILI_QUERY_KEYS
        ]
        return urlunparse(
            (
                parsed.scheme or "https",
                parsed.netloc,
                parsed.path,
                "",
                urlencode(query),
                "",
            )
        )

    def _ydl_class(self):
        try:
            from yt_dlp import YoutubeDL  # type: ignore

            return YoutubeDL
        except Exception as exc:
            raise ExtractorError("缺少 yt-dlp 依赖，请先安装后端依赖。") from exc

    def _options(self, auth: AuthContext | None = None, **extra: Any) -> dict[str, Any]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Origin": "https://www.bilibili.com",
            "Referer": "https://www.bilibili.com/",
            "Sec-Fetch-Site": "same-site",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        }
        if auth and auth.cookie:
            headers["Cookie"] = auth.cookie
        options: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "logger": _YtDlpLogger(),
            "skip_download": True,
            "http_headers": headers,
            "noplaylist": True,
        }
        options.update(extra)
        return options

    def fetch_info(self, url: str, auth: AuthContext | None = None) -> dict[str, Any]:
        if not self.supports(url):
            raise ExtractorError("当前仅支持 B站链接、BV号、av号、ep号或 ss号。")
        YoutubeDL = self._ydl_class()
        normalized_url = self.normalize_url(url)
        try:
            with YoutubeDL(self._options(auth)) as ydl:
                raw = ydl.extract_info(normalized_url, download=False)
        except Exception as exc:
            raise ExtractorError(f"解析失败：{self._friendly_error(exc)}") from exc
        if not raw:
            raise ExtractorError("未解析到视频信息。")
        return self._normalize_preview(normalized_url, raw).model_dump()

    def _friendly_error(self, exc: Exception) -> str:
        message = str(exc)
        if "HTTP Error 412" in message or "Precondition Failed" in message:
            return (
                "B站接口返回 412 Precondition Failed。通常是请求被风控或当前网络环境被拒绝；"
                "请先确认 Cookie 已导入，或稍后更换网络后重试。"
            )
        return message

    def _normalize_preview(self, url: str, raw: dict[str, Any]) -> PreviewResponse:
        formats = raw.get("formats") or []
        videos: list[MediaFormat] = []
        audios: list[MediaFormat] = []
        seen_video: set[str] = set()
        seen_audio: set[str] = set()

        for item in formats:
            format_id = str(item.get("format_id") or "")
            if not format_id:
                continue
            vcodec = item.get("vcodec")
            acodec = item.get("acodec")
            filesize = item.get("filesize") or item.get("filesize_approx")
            ext = item.get("ext")
            tbr = item.get("tbr")
            format_note = item.get("format_note") or ""
            width = item.get("width")
            height = item.get("height")
            resolution = item.get("resolution")
            if not resolution and width and height:
                resolution = f"{width}x{height}"

            if vcodec and vcodec != "none":
                if format_id in seen_video:
                    continue
                seen_video.add(format_id)
                quality = str(format_note or item.get("format") or resolution or item.get("height") or "")
                label_parts = [quality or format_id]
                if resolution and resolution not in label_parts:
                    label_parts.append(str(resolution))
                if vcodec:
                    label_parts.append(str(vcodec).split(".")[0])
                videos.append(
                    MediaFormat(
                        format_id=format_id,
                        label=" · ".join(part for part in label_parts if part),
                        ext=ext,
                        codec=vcodec,
                        quality=quality or None,
                        resolution=resolution,
                        bitrate=tbr,
                        fps=item.get("fps"),
                        filesize=filesize,
                    )
                )
            elif acodec and acodec != "none":
                if format_id in seen_audio:
                    continue
                seen_audio.add(format_id)
                quality = str(format_note or (f"{round(tbr)}K" if tbr else "") or format_id)
                audios.append(
                    MediaFormat(
                        format_id=format_id,
                        label=" · ".join(part for part in [quality, str(acodec).split(".")[0]] if part),
                        ext=ext,
                        codec=acodec,
                        quality=quality or None,
                        bitrate=tbr,
                        filesize=filesize,
                    )
                )

        videos.sort(key=lambda item: (item.bitrate or 0, item.fps or 0), reverse=True)
        audios.sort(key=lambda item: item.bitrate or 0, reverse=True)

        return PreviewResponse(
            url=url,
            title=raw.get("title") or "未命名视频",
            uploader=raw.get("uploader") or raw.get("uploader_id"),
            duration=raw.get("duration"),
            thumbnail=raw.get("thumbnail"),
            webpage_url=raw.get("webpage_url") or raw.get("original_url"),
            videos=videos,
            audios=audios,
        )

    def download(self, task: dict[str, Any], auth: AuthContext | None = None, progress_hook=None) -> str:
        options = task["options"]
        output_dir = Path(task["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        title = sanitize_filename(task.get("title") or "bilibili-video")
        merge = bool(options.get("merge", True))
        container = options.get("container", "mp4")
        video_format_id = options.get("video_format_id")
        audio_format_id = options.get("audio_format_id")
        audio_output_format = options.get("audio_output_format", "m4a")
        download_cover = bool(options.get("download_cover", False))
        thumbnail_url = options.get("thumbnail_url")
        if not video_format_id and not audio_format_id and not download_cover:
            raise ExtractorError("请至少选择一个视频、音频或封面资源。")
        if video_format_id and not audio_format_id:
            raise ExtractorError("下载视频需要同时选择音频流，否则文件会没有声音。")

        if merge and video_format_id and audio_format_id and not ffmpeg_available():
            raise ExtractorError("未检测到 FFmpeg，无法合并音视频。")

        outputs: list[str] = []
        if download_cover:
            if not thumbnail_url:
                raise ExtractorError("当前资源没有可下载的封面。")
            try:
                payload = fetch_thumbnail(str(thumbnail_url))
            except MediaProxyError as exc:
                raise ExtractorError(f"封面下载失败：{exc}") from exc
            cover_path = output_dir / f"{title}.cover.{_thumbnail_extension(payload)}"
            cover_path.write_bytes(payload.content)
            outputs.append(str(cover_path))

        if not video_format_id and not audio_format_id:
            return "\n".join(outputs)

        YoutubeDL = self._ydl_class()
        download_options = self._options(
            auth,
            skip_download=False,
            progress_hooks=[progress_hook] if progress_hook else [],
            continuedl=True,
            retries=3,
            fragment_retries=3,
            merge_output_format=container,
            outtmpl=str(output_dir / f"{title}.%(ext)s"),
        )

        if merge and video_format_id and audio_format_id:
            download_options["format"] = f"{video_format_id}+{audio_format_id}"
            with YoutubeDL(download_options) as ydl:
                ydl.download([self.normalize_url(task["url"])])
            outputs.append(str(output_dir / f"{title}.{container}"))
            return "\n".join(outputs)

        for label, format_id in (("video", video_format_id), ("audio", audio_format_id)):
            if not format_id:
                continue
            single_options = dict(download_options)
            single_options["format"] = format_id
            output_stem = f"{title}.{label}"
            before = {
                path
                for path in output_dir.iterdir()
                if path.is_file() and path.name.startswith(f"{output_stem}.")
            }
            single_options["outtmpl"] = str(output_dir / f"{output_stem}.%(ext)s")
            with YoutubeDL(single_options) as ydl:
                ydl.download([self.normalize_url(task["url"])])
            downloaded_path = _find_downloaded_file(output_dir, output_stem, before)
            if label == "audio" and audio_output_format == "mp3":
                source_path = downloaded_path
                target_path = output_dir / f"{output_stem}.mp3"
                if source_path.suffix.lower() != ".mp3":
                    downloaded_path = _convert_audio_to_mp3(source_path, target_path)
                if source_path != downloaded_path and source_path.exists():
                    source_path.unlink()
            outputs.append(str(downloaded_path))
        return "\n".join(outputs)
