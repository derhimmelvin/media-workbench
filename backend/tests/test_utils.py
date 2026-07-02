from app.utils import (
    mask_cookie,
    resolve_task_output_directory,
    sanitize_filename,
    unique_path,
)


def test_sanitize_filename_removes_illegal_chars():
    assert sanitize_filename('a/b:c*?"<>|\n title') == "a_b_c_ title"


def test_sanitize_filename_uses_fallback_for_empty_values():
    assert sanitize_filename("   ... ", fallback="fallback") == "fallback"


def test_unique_path_appends_counter_for_existing_file(tmp_path):
    existing = tmp_path / "Example.mp4"
    existing.write_bytes(b"video")

    assert unique_path(existing) == tmp_path / "Example-2.mp4"


def test_mask_cookie_never_returns_raw_cookie():
    raw = "SESSDATA=secret; bili_jct=token"
    masked = mask_cookie(raw)
    assert "secret" not in masked
    assert "token" not in masked
    assert masked.startswith("SESSDATA=***#")


def test_resolve_task_output_directory_uses_existing_output_file(tmp_path):
    output_file = tmp_path / "downloads" / "video.mp4"
    output_file.parent.mkdir()
    output_file.write_bytes(b"video")

    assert resolve_task_output_directory(str(output_file), tmp_path) == output_file.parent.resolve()


def test_resolve_task_output_directory_uses_first_existing_parent(tmp_path):
    output_dir = tmp_path / "downloads"
    output_dir.mkdir()
    output_path = f"{output_dir / 'missing.mp4'}\n{output_dir / 'audio.mp3'}"

    assert resolve_task_output_directory(output_path, tmp_path) == output_dir.resolve()


def test_resolve_task_output_directory_falls_back_to_output_dir(tmp_path):
    output_dir = tmp_path / "downloads"
    output_dir.mkdir()

    assert resolve_task_output_directory(None, output_dir) == output_dir.resolve()
