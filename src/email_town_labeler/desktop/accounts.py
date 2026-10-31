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
                raise ValueError("Account configuration could not be read. Keep the file and check it.")
            self.rows = data["accounts"]
        elif default_paths.token.exists():
            self.add(
                "Primary Gmail",
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
        raise ValueError("Account not found")

    @staticmethod
    def name(value) -> str:
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > 80
            or any(ord(c) < 32 for c in value)
        ):
            raise ValueError("Enter an account name from 1–80 characters")
        return value.strip()

    def add(self, name: str, provider: str, settings: dict, *, account_id: str | None = None) -> dict:
        name = self.name(name)
        if provider not in ("gmail", "imap", "microsoft"):
            raise ValueError("Unsupported email provider")
        with self.lock:
            if provider == "gmail":
                target = str(Path(settings["config_dir"]).resolve())
                if any(
                    r["provider"] == provider and str(Path(r["settings"]["config_dir"]).resolve()) == target
                    for r in self.rows
                ):
                    raise ValueError("This Gmail configuration is already connected")
            if provider == "imap" and any(
                r["provider"] == provider
                and all(r["settings"].get(k) == settings.get(k) for k in ("host", "username", "port"))
                for r in self.rows
            ):
                raise ValueError("This IMAP account is already connected")
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
                raise ValueError("This mailbox is already connected through another account")
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
            raise ValueError("enabled must be a boolean")
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
            raise ValueError("Choose the absolute directory of a configured account")
        paths = Paths.default(p)
        if not paths.token.is_file():
            raise ValueError("This directory has no signed-in Gmail account")
        return self.add(
            name,
            "gmail",
            {"config_dir": str(paths.config_dir.resolve()), "data_dir": str(paths.data_dir.resolve())},
        )


def imap_settings(data: dict) -> dict:
    host = str(data.get("host", "")).strip().lower()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", host):
        raise ValueError("Enter an IMAP server hostname without a protocol or path")
    try:
        port = int(data.get("port", 993))
    except (ValueError, TypeError) as e:
        raise ValueError("Invalid IMAP port") from e
    if not 1 <= port <= 65535:
        raise ValueError("Invalid IMAP port")
    username = str(data.get("username", "")).strip()
    if not username or len(username) > 320 or any(ord(c) < 32 for c in username):
        raise ValueError("Enter the mailbox username")
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
            raise ValueError("The system credential vault is unavailable. Enable it and try again")
        if password is not None:
            keyring.set_password("email-town-workbench", account_id, password)
            return password
        value = keyring.get_password("email-town-workbench", account_id)
        if not value:
            raise ValueError("No app-specific password was found. Reconnect the account")
        return value
    except ImportError as e:
        raise ValueError("IMAP connections require email-town-labeler[desktop]") from e
