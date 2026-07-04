from __future__ import annotations

import re
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .base import AuthContext, BaseExtractor, ExtractorError
from ..media import MediaProxyError, ThumbnailPayload, fetch_thumbnail
from ..schemas import MediaFormat, PreviewResponse, SubtitleTrack
from ..utils import (
    ffmpeg_available,
    sanitize_filename,
    unique_path,
)


BILIBILI_ID_PATTERN = re.compile(r"^(BV[0-9A-Za-z]+|av\d+|ep\d+|ss\d+)$", re.IGNORECASE)
BILIBILI_VIDEO_PATH_PATTERN = re.compile(r"^/video/(BV[0-9A-Za-z]+|av\d+)(?:/|$)", re.IGNORECASE)
BILIBILI_BANGUMI_PATH_PATTERN = re.compile(r"^/bangumi/play/(ep\d+|ss\d+)(?:/|$)", re.IGNORECASE)
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
SAFE_BILIBILI_QUERY_KEYS = {"p", "page", "bvid", "aid", "cid", "ep_id", "season_id"}
COOKIE_ATTRIBUTE_NAMES = {"domain", "path", "expires", "max-age", "secure", "httponly", "samesite"}
UNSUPPORTED_INPUT_MESSAGE = "这不是可解析的视频链接。请填写 B站视频链接、BV号、av号、ep号或 ss号。"
SUPPORTED_SUBTITLE_OUTPUT_FORMATS = {"srt", "txt"}
SUPPORTED_SUBTITLE_SOURCE_FORMATS = {"srt", "vtt"}
SUBTITLE_ID_PATTERN = re.compile(r"^(normal|automatic):([^:]+)$")
SUBTITLE_LANGUAGE_LABELS = {
    "zh": "中文",
    "zh-CN": "中文",
    "zh-Hans": "中文（简体）",
    "zh-Hant": "中文（繁体）",
    "en": "英语",
    "ja": "日语",
    "ko": "韩语",
}


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


def _stem_exists(output_dir: Path, stem: str) -> bool:
    return any(path.name == stem or path.name.startswith(f"{stem}.") for path in output_dir.iterdir())


def _unique_stem(output_dir: Path, stem: str) -> str:
    candidate = stem
    if not _stem_exists(output_dir, candidate):
        return candidate
    for index in range(2, 10000):
        candidate = f"{stem}-{index}"
        if not _stem_exists(output_dir, candidate):
            return candidate
    raise ExtractorError(f"无法生成不冲突的文件名：{stem}")


def _output_stem(
    output_dir: Path,
    task: dict[str, Any],
    options: dict[str, Any],
    resource: str,
    distinct_resource: bool = False,
) -> str:
    fallback_title = sanitize_filename(task.get("title") or "bilibili-video", "bilibili-video")
    stem = sanitize_filename(str(options.get("custom_filename") or ""), fallback=fallback_title)
    if distinct_resource:
        stem = sanitize_filename(f"{stem}.{resource}", fallback=resource)
    return _unique_stem(output_dir, stem)


def _looks_like_netscape_cookie_file(cookie: str) -> bool:
    lines = [line.strip() for line in cookie.splitlines() if line.strip()]
    if not lines:
        return False
    if any(line.startswith("# Netscape HTTP Cookie File") for line in lines):
        return True
    return any(len(line.split("\t")) >= 7 for line in lines if not line.startswith("#"))


def _cookie_pairs(cookie: str) -> list[tuple[str, str]]:
    raw = re.sub(r"^Cookie:\s*", "", cookie.strip(), flags=re.IGNORECASE)
    raw = raw.replace("\r", "\n").replace("\n", "; ")
    pairs: list[tuple[str, str]] = []
    for segment in raw.split(";"):
        name, separator, value = segment.partition("=")
        if not separator:
            continue
        name = name.strip()
        if not name or name.lower() in COOKIE_ATTRIBUTE_NAMES:
            continue
        sanitized_name = name.replace("\t", " ").replace("\n", " ").strip()
        sanitized_value = value.strip().replace("\t", " ").replace("\n", " ")
        if sanitized_name:
            pairs.append((sanitized_name, sanitized_value))
    return pairs


def _netscape_cookie_content(cookie: str) -> str:
    stripped = cookie.strip()
    if _looks_like_netscape_cookie_file(stripped):
        return stripped + ("\n" if not stripped.endswith("\n") else "")
    pairs = _cookie_pairs(stripped)
    if not pairs:
        return ""
    lines = [
        "# Netscape HTTP Cookie File",
        "# This temporary file is generated for yt-dlp and deleted after use.",
    ]
    for name, value in pairs:
        lines.append(f".bilibili.com\tTRUE\t/\tFALSE\t2147483647\t{name}\t{value}")
    return "\n".join(lines) + "\n"


def _media_height(item: MediaFormat) -> int:
    for source in (item.resolution, item.quality, item.label):
        if not source:
            continue
        resolution_match = re.search(r"[xX]\s*(\d{3,4})", source)
        if resolution_match:
            return int(resolution_match.group(1))
        quality_match = re.search(r"(?<!\d)(\d{3,4})\s*[pP]", source)
        if quality_match:
            return int(quality_match.group(1))
    return 0


def _subtitle_formats(entries: Any) -> list[str]:
    if not isinstance(entries, list):
        return []
    formats: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        extension = str(entry.get("ext") or "").lower()
        if extension in SUPPORTED_SUBTITLE_SOURCE_FORMATS and extension not in formats:
            formats.append(extension)
    return formats


def _subtitle_label(language: str, source: str) -> str:
    label = SUBTITLE_LANGUAGE_LABELS.get(language, language)
    return f"{label} · 自动字幕" if source == "automatic" else label


def _normalize_subtitle_tracks(raw: dict[str, Any]) -> list[SubtitleTrack]:
    tracks: list[SubtitleTrack] = []
    normal_languages: set[str] = set()

    for language, entries in (raw.get("subtitles") or {}).items():
        language = str(language)
        if language == "danmaku":
            continue
        formats = _subtitle_formats(entries)
        if not formats:
            continue
        normal_languages.add(language)
        tracks.append(
            SubtitleTrack(
                id=f"normal:{language}",
                language=language,
                label=_subtitle_label(language, "normal"),
                source="normal",
                formats=formats,
            )
        )

    for language, entries in (raw.get("automatic_captions") or {}).items():
        language = str(language)
        if language == "danmaku" or language in normal_languages:
            continue
        formats = _subtitle_formats(entries)
        if not formats:
            continue
        tracks.append(
            SubtitleTrack(
                id=f"automatic:{language}",
                language=language,
                label=_subtitle_label(language, "automatic"),
                source="automatic",
                formats=formats,
            )
        )

    return tracks


def _subtitle_languages_by_source(track_ids: list[str]) -> tuple[set[str], set[str]]:
    normal_languages: set[str] = set()
    automatic_languages: set[str] = set()
    for track_id in track_ids:
        match = SUBTITLE_ID_PATTERN.match(track_id)
        if not match:
            raise ExtractorError("字幕选项无效，请重新解析后选择字幕。")
        source, language = match.groups()
        if language == "danmaku":
            raise ExtractorError("弹幕不属于当前字幕下载范围。")
        if source == "normal":
            normal_languages.add(language)
        else:
            automatic_languages.add(language)
    return normal_languages, automatic_languages


def _find_downloaded_subtitles(
    output_dir: Path,
    stem: str,
    before: set[Path],
    languages: set[str],
    subtitle_formats: set[str],
) -> list[Path]:
    candidates = [
        path
        for path in output_dir.iterdir()
        if path.is_file()
        and path not in before
        and path.name.startswith(f"{stem}.")
        and path.suffix.lower().lstrip(".") in subtitle_formats
        and any(path.name.endswith(f".{language}.{subtitle_format}") for language in languages for subtitle_format in subtitle_formats)
    ]
    return sorted(candidates, key=lambda path: path.name)


def _plain_subtitle_text(content: str, extension: str) -> str:
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    output: list[str] = []
    skip_block = False

    for index, line in enumerate(lines):
        stripped = line.strip().lstrip("\ufeff")
        if not stripped:
            skip_block = False
            continue
        if stripped == "WEBVTT" or stripped.startswith("WEBVTT "):
            continue
        if stripped.startswith(("NOTE", "STYLE", "REGION")):
            skip_block = True
            continue
        if skip_block:
            continue
        if "-->" in stripped:
            continue
        if extension == "srt" and stripped.isdigit():
            continue
        if index + 1 < len(lines) and "-->" in lines[index + 1]:
            continue

        text = unescape(re.sub(r"<[^>]+>", "", stripped)).strip()
        if text:
            output.append(text)

    return "\n".join(output) + ("\n" if output else "")


def _convert_subtitle_to_txt(source_path: Path, target_path: Path) -> Path:
    extension = source_path.suffix.lower().lstrip(".")
    content = source_path.read_text(encoding="utf-8-sig", errors="replace")
    target_path.write_text(_plain_subtitle_text(content, extension), encoding="utf-8")
    return target_path


class YtDlpBilibiliExtractor(BaseExtractor):
    def supports(self, url: str) -> bool:
        stripped = url.strip()
        if BILIBILI_ID_PATTERN.match(stripped):
            return True

        parsed = urlparse(stripped)
        netloc = parsed.netloc.lower()
        if netloc == "b23.tv" or netloc.endswith(".b23.tv"):
            return True
        if not netloc.endswith("bilibili.com"):
            return False
        if BILIBILI_VIDEO_PATH_PATTERN.match(parsed.path):
            return True
        if BILIBILI_BANGUMI_PATH_PATTERN.match(parsed.path):
            return True

        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        return bool(
            BILIBILI_ID_PATTERN.match(query.get("bvid", ""))
            or re.match(r"^\d+$", query.get("aid", ""))
            or re.match(r"^\d+$", query.get("ep_id", ""))
            or re.match(r"^\d+$", query.get("season_id", ""))
        )

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

    @contextmanager
    def _temporary_cookiefile(self, auth: AuthContext | None = None) -> Iterator[str | None]:
        if not auth or not auth.cookie:
            yield None
            return
        content = _netscape_cookie_content(auth.cookie)
        if not content:
            yield None
            return
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            prefix="bilibili-cookie-",
            suffix=".txt",
            delete=False,
        )
        try:
            with handle:
                handle.write(content)
            yield handle.name
        finally:
            try:
                Path(handle.name).unlink(missing_ok=True)
            except OSError:
                pass

    @contextmanager
    def _ydl_options(self, auth: AuthContext | None = None, **extra: Any) -> Iterator[dict[str, Any]]:
        with self._temporary_cookiefile(auth) as cookiefile:
            options = self._options(auth, **extra)
            if cookiefile:
                options["cookiefile"] = cookiefile
            yield options

    def fetch_info(self, url: str, auth: AuthContext | None = None) -> dict[str, Any]:
        if not self.supports(url):
            raise ExtractorError(UNSUPPORTED_INPUT_MESSAGE)
        YoutubeDL = self._ydl_class()
        normalized_url = self.normalize_url(url)
        try:
            with self._ydl_options(auth, listsubtitles=True) as options:
                with YoutubeDL(options) as ydl:
                    raw = ydl.extract_info(normalized_url, download=False)
        except Exception as exc:
            raise ExtractorError(f"解析失败：{self._friendly_error(exc)}") from exc
        if not raw:
            raise ExtractorError("未解析到视频信息。")
        return self._normalize_preview(normalized_url, raw).model_dump()

    def _friendly_error(self, exc: Exception) -> str:
        message = ANSI_ESCAPE_PATTERN.sub("", str(exc)).strip()
        if "Unsupported URL" in message:
            return UNSUPPORTED_INPUT_MESSAGE
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

        videos.sort(key=lambda item: (_media_height(item), item.bitrate or 0, item.fps or 0), reverse=True)
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
            subtitles=_normalize_subtitle_tracks(raw),
        )

    def download(self, task: dict[str, Any], auth: AuthContext | None = None, progress_hook=None) -> str:
        options = task["options"]
        output_dir = Path(task["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        merge = bool(options.get("merge", True))
        container = options.get("container", "mp4")
        video_format_id = options.get("video_format_id")
        audio_format_id = options.get("audio_format_id")
        audio_output_format = options.get("audio_output_format", "m4a")
        download_cover = bool(options.get("download_cover", False))
        thumbnail_url = options.get("thumbnail_url")
        subtitle_track_ids = [str(track_id) for track_id in options.get("subtitle_track_ids") or [] if str(track_id)]
        subtitle_format = str(options.get("subtitle_format") or "srt").lower()
        has_media_output = bool(video_format_id or audio_format_id)
        has_subtitle_output = bool(subtitle_track_ids)
        selected_output_count = sum(1 for enabled in (has_media_output, download_cover, has_subtitle_output) if enabled)
        has_multiple_outputs = selected_output_count > 1 or bool(video_format_id and audio_format_id and not merge)
        if subtitle_format not in SUPPORTED_SUBTITLE_OUTPUT_FORMATS:
            raise ExtractorError("字幕输出格式仅支持 srt 或 txt。")
        if not video_format_id and not audio_format_id and not download_cover and not has_subtitle_output:
            raise ExtractorError("请至少选择一个视频、音频、封面或字幕资源。")
        if video_format_id and not audio_format_id:
            raise ExtractorError("下载视频需要同时选择音频流，否则文件会没有声音。")

        if merge and video_format_id and audio_format_id and not ffmpeg_available():
            raise ExtractorError("未检测到 FFmpeg，无法合并音视频。")

        outputs: list[str] = []
        subtitle_stem = (
            _output_stem(output_dir, task, options, "subtitle", distinct_resource=False)
            if has_subtitle_output
            else None
        )
        if download_cover:
            if not thumbnail_url:
                raise ExtractorError("当前资源没有可下载的封面。")
            try:
                payload = fetch_thumbnail(str(thumbnail_url))
            except MediaProxyError as exc:
                raise ExtractorError(f"封面下载失败：{exc}") from exc
            cover_stem = _output_stem(output_dir, task, options, "cover", distinct_resource=has_multiple_outputs)
            cover_path = unique_path(output_dir / f"{cover_stem}.{_thumbnail_extension(payload)}")
            cover_path.write_bytes(payload.content)
            outputs.append(str(cover_path))

        if not video_format_id and not audio_format_id:
            if has_subtitle_output and subtitle_stem:
                outputs.extend(
                    self._download_subtitles(
                        task,
                        auth,
                        subtitle_track_ids,
                        subtitle_format,
                        output_dir,
                        subtitle_stem,
                    )
                )
            return "\n".join(outputs)

        YoutubeDL = self._ydl_class()
        ydl_options_context = self._ydl_options(
            auth,
            skip_download=False,
            progress_hooks=[progress_hook] if progress_hook else [],
            continuedl=True,
            retries=3,
            fragment_retries=3,
            merge_output_format=container,
        )

        with ydl_options_context as download_options:
            if merge and video_format_id and audio_format_id:
                output_stem = _output_stem(output_dir, task, options, "video")
                before = {
                    path
                    for path in output_dir.iterdir()
                    if path.is_file() and path.name.startswith(f"{output_stem}.")
                }
                download_options["format"] = f"{video_format_id}+{audio_format_id}"
                download_options["outtmpl"] = str(output_dir / f"{output_stem}.%(ext)s")
                with YoutubeDL(download_options) as ydl:
                    ydl.download([self.normalize_url(task["url"])])
                outputs.append(str(_find_downloaded_file(output_dir, output_stem, before)))
            else:
                for label, format_id in (("video", video_format_id), ("audio", audio_format_id)):
                    if not format_id:
                        continue
                    single_options = dict(download_options)
                    single_options["format"] = format_id
                    output_stem = _output_stem(output_dir, task, options, label, distinct_resource=has_multiple_outputs)
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
                        target_path = unique_path(output_dir / f"{output_stem}.mp3")
                        if source_path.suffix.lower() != ".mp3":
                            downloaded_path = _convert_audio_to_mp3(source_path, target_path)
                        if source_path != downloaded_path and source_path.exists():
                            source_path.unlink()
                    outputs.append(str(downloaded_path))
        if has_subtitle_output and subtitle_stem:
            outputs.extend(
                self._download_subtitles(
                    task,
                    auth,
                    subtitle_track_ids,
                    subtitle_format,
                    output_dir,
                    subtitle_stem,
                )
            )
        return "\n".join(outputs)

    def _download_subtitles(
        self,
        task: dict[str, Any],
        auth: AuthContext | None,
        subtitle_track_ids: list[str],
        subtitle_format: str,
        output_dir: Path,
        output_stem: str,
    ) -> list[str]:
        normal_languages, automatic_languages = _subtitle_languages_by_source(subtitle_track_ids)
        languages = normal_languages | automatic_languages
        if not languages:
            return []
        before = {
            path
            for path in output_dir.iterdir()
            if path.is_file() and path.name.startswith(f"{output_stem}.")
        }
        target_subtitle_format = "srt" if subtitle_format == "txt" else subtitle_format
        with self._ydl_options(
            auth,
            skip_download=True,
            writesubtitles=bool(normal_languages),
            writeautomaticsub=bool(automatic_languages),
            subtitleslangs=sorted(languages),
            subtitlesformat=f"{target_subtitle_format}/vtt/best",
            outtmpl=str(output_dir / f"{output_stem}.%(ext)s"),
            postprocessors=[] if subtitle_format == "txt" else [
                {
                    "key": "FFmpegSubtitlesConvertor",
                    "format": subtitle_format,
                    "when": "before_dl",
                }
            ],
        ) as subtitle_options:
            with self._ydl_class()(subtitle_options) as ydl:
                ydl.download([self.normalize_url(task["url"])])

        if subtitle_format == "txt":
            source_paths = _find_downloaded_subtitles(output_dir, output_stem, before, languages, {"srt", "vtt"})
            paths = []
            for source_path in source_paths:
                target_path = unique_path(source_path.with_suffix(".txt"))
                paths.append(_convert_subtitle_to_txt(source_path, target_path))
                if source_path != target_path:
                    source_path.unlink(missing_ok=True)
        else:
            paths = _find_downloaded_subtitles(output_dir, output_stem, before, languages, {subtitle_format})
        if not paths:
            raise ExtractorError("未下载到所选字幕，请重新解析后选择可用字幕。")
        return [str(path) for path in paths]
