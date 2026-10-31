"""Explicit local account enrollment. Registry contains settings, not passwords or email content."""

from __future__ import annotations

import json
import re
import threading
import uuid
from pathlib import Path

from ..config import Paths
from ..gmail.auth import _write_private


class Accounts:
    def __init__(self, root: Path, default_paths: Paths):
        self.root, self.default_paths = root, default_paths
        self.path = root / "accounts.json"
        self.lock = threading.RLock()
        root.mkdir(parents=True, exist_ok=True)
        root.chmod(0o700)
        self.rows = []
        if self.path.exists():
            data = json.loads(self.path.read_text())
            if data.get("version") != 1 or not isinstance(data.get("accounts"), list):
                raise ValueError("账户配置无法读取；请保留文件后检查")
            self.rows = data["accounts"]
        elif default_paths.token.exists():
            self.add(
                "当前 Gmail",
                "gmail",
                {
                    "config_dir": str(default_paths.config_dir.absolute()),
                    "data_dir": str(default_paths.data_dir.absolute()),
                },
            )

    def _save(self):
        _write_private(self.path, json.dumps({"version": 1, "accounts": self.rows}, ensure_ascii=False))

    def all(self) -> list[dict]:
        with self.lock:
            return json.loads(json.dumps(self.rows))

    def get(self, account_id: str) -> dict:
        return next((r for r in self.all() if r["id"] == account_id), None) or self._missing()

    @staticmethod
    def _missing():
        raise ValueError("账户不存在")

    @staticmethod
    def name(value) -> str:
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > 80
            or any(ord(c) < 32 for c in value)
        ):
            raise ValueError("请填写 1–80 字的账户名称")
        return value.strip()

    def add(self, name: str, provider: str, settings: dict, *, account_id: str | None = None) -> dict:
        name = self.name(name)
        if provider not in ("gmail", "imap", "microsoft"):
            raise ValueError("不支持的邮箱类型")
        with self.lock:
            if provider == "gmail":
                target = str(Path(settings["config_dir"]).resolve())
                if any(
                    r["provider"] == provider and str(Path(r["settings"]["config_dir"]).resolve()) == target
                    for r in self.rows
                ):
                    raise ValueError("此 Gmail 配置已连接")
            if provider == "imap" and any(
                r["provider"] == provider
                and all(r["settings"].get(k) == settings.get(k) for k in ("host", "username", "port"))
                for r in self.rows
            ):
                raise ValueError("此 IMAP 账户已连接")
            row = {
                "id": account_id or uuid.uuid4().hex,
                "name": name,
                "provider": provider,
                "settings": settings,
                "enabled": True,
            }
            self.rows.append(row)
            try:
                self._save()
            except BaseException:
                self.rows.pop()
                raise
            return dict(row)

    def bind_identity(self, account_id: str, identity: str):
        """Detect a second authorization of the same mailbox without storing its address."""
        with self.lock:
            row = next((r for r in self.rows if r["id"] == account_id), None)
            if row is None:
                self._missing()
            if any(
                r["id"] != account_id and r["provider"] == row["provider"] and r.get("identity") == identity
                for r in self.rows
            ):
                raise ValueError("此邮箱已通过另一个账户连接，请使用已连接的账户")
            if row.get("identity") != identity:
                old = row.get("identity")
                row["identity"] = identity
                try:
                    self._save()
                except BaseException:
                    if old is None:
                        row.pop("identity", None)
                    else:
                        row["identity"] = old
                    raise

    def replace_settings(self, account_id: str, name: str, settings: dict):
        name = self.name(name)
        with self.lock:
            row = next((r for r in self.rows if r["id"] == account_id), None)
            if row is None:
                self._missing()
            old = dict(row)
            row.update(name=name, settings=dict(settings), enabled=True)
            try:
                self._save()
            except BaseException:
                row.clear()
                row.update(old)
                raise

    def enable(self, account_id: str, enabled: bool):
        if type(enabled) is not bool:
            raise ValueError("enabled 必须为布尔值")
        with self.lock:
            row = next((r for r in self.rows if r["id"] == account_id), None)
            if row is None:
                self._missing()
            old = row["enabled"]
            row["enabled"] = enabled
            try:
                self._save()
            except BaseException:
                row["enabled"] = old
                raise

    def enroll_home(self, name: str, home: str) -> dict:
        p = Path(home).expanduser()
        if not p.is_absolute():
            raise ValueError("请选择已配置账户的绝对目录")
        paths = Paths.default(p)
        if not paths.token.is_file():
            raise ValueError("此目录中没有已登录的 Gmail 账户")
        return self.add(
            name,
            "gmail",
            {"config_dir": str(paths.config_dir.resolve()), "data_dir": str(paths.data_dir.resolve())},
        )


def imap_settings(data: dict) -> dict:
    host = str(data.get("host", "")).strip().lower()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", host):
        raise ValueError("请填写 IMAP 服务器域名，不包含协议或路径")
    try:
        port = int(data.get("port", 993))
    except (ValueError, TypeError) as e:
        raise ValueError("IMAP 端口无效") from e
    if not 1 <= port <= 65535:
        raise ValueError("IMAP 端口无效")
    username = str(data.get("username", "")).strip()
    if not username or len(username) > 320 or any(ord(c) < 32 for c in username):
        raise ValueError("请填写邮箱用户名")
    return {"host": host, "port": port, "username": username}


def password_store(account_id: str, password: str | None = None) -> str:
    try:
        import keyring

        # Reject plaintext/third-party fallback backends: the desktop extra must use the OS vault.
        module = type(keyring.get_keyring()).__module__
        if not module.startswith(
            (
                "keyring.backends.macOS",
                "keyring.backends.Windows",
                "keyring.backends.SecretService",
                "keyring.backends.kwallet",
            )
        ):
            raise ValueError("系统钥匙串不可用；请启用系统凭据服务后重试")
        if password is not None:
            keyring.set_password("email-town-workbench", account_id, password)
            return password
        value = keyring.get_password("email-town-workbench", account_id)
        if not value:
            raise ValueError("找不到此账户的应用专用密码，请重新连接")
        return value
    except ImportError as e:
        raise ValueError("连接 IMAP 需要安装 email-town-labeler[desktop]") from e
