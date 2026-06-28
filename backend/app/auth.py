from __future__ import annotations

from dataclasses import dataclass

from .config import AppConfig
from .db import Database
from .utils import mask_cookie


class CredentialStoreError(RuntimeError):
    pass


@dataclass
class CookieState:
    configured: bool
    masked: str | None
    keyring_available: bool
    message: str | None = None


class CredentialStore:
    def __init__(self, db: Database, config: AppConfig):
        self.db = db
        self.config = config

    def _keyring(self):
        try:
            import keyring  # type: ignore

            return keyring
        except Exception as exc:
            raise CredentialStoreError("系统钥匙串不可用，请安装 keyring 依赖。") from exc

    def is_available(self) -> bool:
        try:
            keyring = self._keyring()
            keyring.get_keyring()
            return True
        except Exception:
            return False

    def save_bilibili_cookie(self, cookie: str) -> CookieState:
        cookie = cookie.strip()
        if not cookie:
            raise CredentialStoreError("Cookie 不能为空。")
        keyring = self._keyring()
        try:
            keyring.set_password(
                self.config.service_name,
                self.config.bilibili_cookie_username,
                cookie,
            )
        except Exception as exc:
            raise CredentialStoreError("无法写入系统钥匙串，已拒绝明文保存 Cookie。") from exc

        masked = mask_cookie(cookie)
        self.db.set_setting("bilibili_cookie_masked", masked)
        return CookieState(True, masked, True)

    def get_bilibili_cookie(self) -> str | None:
        keyring = self._keyring()
        try:
            return keyring.get_password(
                self.config.service_name,
                self.config.bilibili_cookie_username,
            )
        except Exception as exc:
            raise CredentialStoreError("无法读取系统钥匙串中的 Cookie。") from exc

    def delete_bilibili_cookie(self) -> CookieState:
        keyring = self._keyring()
        try:
            keyring.delete_password(
                self.config.service_name,
                self.config.bilibili_cookie_username,
            )
        except keyring.errors.PasswordDeleteError:
            pass
        except Exception as exc:
            raise CredentialStoreError("无法删除系统钥匙串中的 Cookie。") from exc
        self.db.delete_setting("bilibili_cookie_masked")
        return CookieState(False, None, True)

    def status(self) -> CookieState:
        if not self.is_available():
            return CookieState(False, None, False, "系统钥匙串不可用。")
        try:
            cookie = self.get_bilibili_cookie()
        except CredentialStoreError as exc:
            return CookieState(False, None, True, str(exc))
        masked = self.db.get_setting("bilibili_cookie_masked")
        if cookie and not masked:
            masked = mask_cookie(cookie)
            self.db.set_setting("bilibili_cookie_masked", masked)
        return CookieState(bool(cookie), masked if cookie else None, True)
