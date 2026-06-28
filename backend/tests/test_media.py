import pytest

from app.media import MediaProxyError, normalize_thumbnail_url


def test_normalize_thumbnail_url_accepts_bilibili_cdn_http_url():
    assert normalize_thumbnail_url("http://i0.hdslb.com/bfs/archive/cover.jpg") == (
        "https://i0.hdslb.com/bfs/archive/cover.jpg"
    )


def test_normalize_thumbnail_url_accepts_protocol_relative_url():
    assert normalize_thumbnail_url("//i1.hdslb.com/bfs/archive/cover.jpg") == (
        "https://i1.hdslb.com/bfs/archive/cover.jpg"
    )


def test_normalize_thumbnail_url_rejects_non_bilibili_hosts():
    with pytest.raises(MediaProxyError):
        normalize_thumbnail_url("https://example.test/cover.jpg")


def test_normalize_thumbnail_url_rejects_non_http_protocols():
    with pytest.raises(MediaProxyError):
        normalize_thumbnail_url("file:///etc/passwd")


def test_normalize_thumbnail_url_strips_userinfo():
    assert normalize_thumbnail_url("https://user:pass@i2.hdslb.com/bfs/archive/cover.jpg") == (
        "https://i2.hdslb.com/bfs/archive/cover.jpg"
    )


def test_normalize_thumbnail_url_rejects_custom_ports():
    with pytest.raises(MediaProxyError):
        normalize_thumbnail_url("https://i0.hdslb.com:8443/bfs/archive/cover.jpg")
