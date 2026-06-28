from __future__ import annotations

from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen


ALLOWED_THUMBNAIL_HOST_SUFFIXES = ("hdslb.com",)
MAX_THUMBNAIL_BYTES = 5 * 1024 * 1024
THUMBNAIL_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.bilibili.com/",
}


@dataclass
class ThumbnailPayload:
    content: bytes
    content_type: str
    source_url: str


class MediaProxyError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def normalize_thumbnail_url(url: str) -> str:
    value = url.strip()
    if value.startswith("//"):
        value = f"https:{value}"

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise MediaProxyError("封面地址协议不受支持。")

    host = (parsed.hostname or "").lower()
    if not any(host == suffix or host.endswith(f".{suffix}") for suffix in ALLOWED_THUMBNAIL_HOST_SUFFIXES):
        raise MediaProxyError("封面地址不属于 B站图片 CDN。")
    if parsed.port not in {None, 80, 443}:
        raise MediaProxyError("封面地址端口不受支持。")

    netloc = host if parsed.port in {None, 443} else f"{host}:80"
    return urlunparse(("https", netloc, parsed.path, "", parsed.query, ""))


def fetch_thumbnail(url: str, timeout: float = 10.0) -> ThumbnailPayload:
    normalized_url = normalize_thumbnail_url(url)
    request = Request(normalized_url, headers=THUMBNAIL_HEADERS)
    try:
        with urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get_content_type()
            content_length = response.headers.get("content-length")
            if content_length and content_length.isdigit() and int(content_length) > MAX_THUMBNAIL_BYTES:
                raise MediaProxyError("封面文件过大。", status_code=502)
            if not content_type.startswith("image/"):
                raise MediaProxyError("远程资源不是图片。", status_code=502)

            content = response.read(MAX_THUMBNAIL_BYTES + 1)
            if len(content) > MAX_THUMBNAIL_BYTES:
                raise MediaProxyError("封面文件过大。", status_code=502)
    except MediaProxyError:
        raise
    except HTTPError as exc:
        raise MediaProxyError(f"封面源站返回 HTTP {exc.code}。", status_code=502) from exc
    except (OSError, URLError) as exc:
        raise MediaProxyError("封面源站不可访问。", status_code=502) from exc

    return ThumbnailPayload(content=content, content_type=content_type, source_url=normalized_url)
