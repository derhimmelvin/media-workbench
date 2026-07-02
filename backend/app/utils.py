from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path
from shutil import which
from typing import Any


ILLEGAL_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')
SPACE_RUN = re.compile(r"\s+")


def now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sanitize_filename(value: str, fallback: str = "download") -> str:
    cleaned = ILLEGAL_FILENAME_CHARS.sub("_", value or "")
    cleaned = SPACE_RUN.sub(" ", cleaned).strip(" .")
    return cleaned[:180] or fallback


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 10000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"无法生成不冲突的文件名：{path}")


def sha256_short(value: str, size: int = 12) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:size]


def mask_cookie(cookie: str) -> str:
    if not cookie:
        return ""
    digest = sha256_short(cookie)
    first_key = cookie.split("=", 1)[0].strip() if "=" in cookie else "cookie"
    return f"{first_key}=***#{digest}"


def ensure_directory(path: str | Path) -> Path:
    target = Path(path).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    return target


def resolve_task_output_directory(output_path: str | None, output_dir: str | Path) -> Path:
    if output_path:
        for value in output_path.splitlines():
            if not value.strip():
                continue
            candidate = Path(value.strip()).expanduser()
            if candidate.exists():
                return candidate.resolve() if candidate.is_dir() else candidate.resolve().parent
            if candidate.parent.exists():
                return candidate.parent.resolve()

    fallback = Path(output_dir).expanduser()
    if fallback.exists() and fallback.is_dir():
        return fallback.resolve()
    raise FileNotFoundError(f"下载目录不存在：{fallback}")


def open_directory(path: str | Path) -> None:
    target = Path(path).expanduser().resolve()
    if not target.exists() or not target.is_dir():
        raise FileNotFoundError(f"目录不存在：{target}")

    if sys.platform == "darwin":
        subprocess.Popen(["open", str(target)])
    elif sys.platform.startswith("win"):
        os.startfile(str(target))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(target)])


def ffmpeg_available() -> bool:
    return which("ffmpeg") is not None


def runtime_versions() -> dict[str, Any]:
    versions: dict[str, Any] = {
        "python": sys.version.split()[0],
        "ffmpeg": which("ffmpeg"),
    }
    try:
        import yt_dlp  # type: ignore

        versions["yt_dlp"] = getattr(yt_dlp, "version", None).__version__
    except Exception:
        versions["yt_dlp"] = None
    try:
        import fastapi  # type: ignore

        versions["fastapi"] = getattr(fastapi, "__version__", None)
    except Exception:
        versions["fastapi"] = None
    return versions
