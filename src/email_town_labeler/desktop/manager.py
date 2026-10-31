"""Local workbench coordinator: bounded background reads, one shared local model, isolated workflow state."""

from __future__ import annotations

import secrets
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path

from ..config import Paths, load_config
from ..gmail.auth import save_credentials
from ..models.worker import load_service_model
from ..policy import decide
from ..taxonomy import LABEL_CLASSES
from .accounts import Accounts, imap_settings, password_store
from .providers import (
    MICROSOFT_TOKEN,
    ImapReader,
    ProviderError,
    create_reader,
    microsoft_device,
    request_json,
    save_microsoft_token,
)
from .state import LocalState, message_key

CATEGORY_NAMES = {
    "action_needed": "需要处理",
    "new_sender": "新发件人",
    "newsletters": "订阅阅读",
    "orders": "订单账单",
    "promotional": "推广优惠",
    "travel": "旅行行程",
    "updates": "通知更新",
    "unknown": "待分类",
}


def safe_error(exc: Exception) -> str:
    if isinstance(exc, (ProviderError, ValueError)):
        # Our own ValueErrors contain no remote mail content. Callers never pass parse errors through.
        return str(exc)[:240]
    return "连接暂时不可用，请检查网络或重新连接账户（" + type(exc).__name__ + "）"


class Workbench:
    def __init__(self, root: Path, default_paths: Paths, *, reader_factory=create_reader, classifier=None):
        self.root, self.paths = root, default_paths
        self.accounts = Accounts(root, default_paths)
        self.state = LocalState(root / "workflow.sqlite")
        self.reader_factory, self.model = reader_factory, classifier
        self.cfg = load_config(default_paths.config_file)
        self.lock, self.model_lock = threading.RLock(), threading.RLock()
        self.reader_locks = {}
        self.readers, self.messages, self.account_status = {}, {}, {}
        self.predictions, self.bodies = {}, OrderedDict()
        self.jobs, self.undos = {}, OrderedDict()
        self.limit, self.refreshing, self.last_refresh = 100, False, None
        self.stop = threading.Event()
        self.model_error = None
        self.demo = False

    def start(self):
        self.refresh()
        threading.Thread(target=self._poll, name="workbench-poll", daemon=True).start()

    def _poll(self):
        while not self.stop.wait(60):
            self.refresh()
            if self.model_lock.acquire(blocking=False):
                try:
                    if self.model is not None and hasattr(self.model, "release_if_idle"):
                        self.model.release_if_idle()
                finally:
                    self.model_lock.release()

    def close(self):
        self.stop.set()
        if self.model_lock.acquire(timeout=1):
            try:
                if self.model is not None and hasattr(self.model, "close"):
                    self.model.close()
            finally:
                self.model_lock.release()

    def snapshot(self) -> dict:
        with self.lock:
            rows = [dict(m) for m in self.messages.values()]
            states = self.state.read([m["id"] for m in rows])
            for row in rows:
                saved = states.get(row["id"], {})
                row["done"] = bool(saved.get("done", False))
                if saved.get("category"):
                    row["category"], row["source"] = saved["category"], "manual"
                row.pop("provider_id", None)
            accounts = []
            for a in self.accounts.all():
                status = dict(
                    self.account_status.get(
                        a["id"],
                        {
                            "status": "waiting",
                            "error": None,
                            "last_sync": None,
                            "loaded": 0,
                            "truncated": False,
                        },
                    )
                )
                connection = {
                    k: a["settings"][k]
                    for k in ("host", "port", "username", "client_id")
                    if k in a["settings"]
                }
                accounts.append(
                    {k: a[k] for k in ("id", "name", "provider", "enabled")}
                    | status
                    | {"connection": connection}
                )
            return {
                "accounts": accounts,
                "messages": sorted(rows, key=lambda m: m["date"], reverse=True),
                "categories": [{"id": c, "name": CATEGORY_NAMES[c]} for c in LABEL_CLASSES],
                "refreshing": self.refreshing,
                "last_refresh": self.last_refresh,
                "jobs": list(self.jobs.values()),
                "demo": self.demo,
                "model_error": self.model_error,
                "limit": self.limit,
            }

    def refresh(self, *, more=False):
        with self.lock:
            if more:
                self.limit = min(1000, self.limit + 100)
            if self.refreshing or self.stop.is_set():
                return
            self.refreshing = True
            self.model_error = None
        threading.Thread(target=self._refresh, name="workbench-refresh", daemon=True).start()

    def _refresh(self):
        semaphore = threading.Semaphore(3)

        def update(account):
            with semaphore:
                if not self.stop.is_set():
                    self._account(account)

        workers = [
            threading.Thread(target=update, args=(a,), daemon=True)
            for a in self.accounts.all()
            if a["enabled"]
        ]
        try:
            for worker in workers:
                worker.start()
            for worker in workers:
                while worker.is_alive() and not self.stop.is_set():
                    worker.join(0.2)
        finally:
            with self.lock:
                self.refreshing, self.last_refresh = False, int(time.time() * 1000)

    def _account(self, account):
        aid = account["id"]
        with self.lock:
            self.account_status.setdefault(aid, {"loaded": 0, "last_sync": None, "truncated": False})
            self.account_status[aid].update(status="syncing", error=None)
            reader_lock = self.reader_locks.setdefault(aid, threading.RLock())
        try:
            with reader_lock:
                reader = self.readers.get(aid)
                if reader is None:
                    reader = self.reader_factory(account)
                    self.readers[aid] = reader
                records, truncated = reader.list(self.limit)
                if getattr(reader, "identity", None):
                    try:
                        self.accounts.bind_identity(aid, reader.identity)
                    except ValueError as exc:
                        raise ProviderError(str(exc)) from exc
            incoming = {}
            for rec in records:
                key = message_key(aid, rec["provider_id"])
                category = rec.get("category")
                prediction = self.predictions.get(key)
                source = "mailbox" if category else "model" if prediction else "pending"
                if not category:
                    category = prediction[0] if prediction else "unknown"
                incoming[key] = {
                    "id": key,
                    "account_id": aid,
                    "provider_id": rec["provider_id"],
                    "subject": rec.get("headers", {}).get("subject", "")[:2000] or "（无主题）",
                    "sender": rec.get("headers", {}).get("from", "")[:1000],
                    "date": rec.get("internal_date", 0),
                    "category": category,
                    "done": False,
                    "unread": "UNREAD" in rec.get("label_ids", []),
                    "source": source,
                    "confidence": prediction[1] if prediction else None,
                }
            with self.lock:
                if not self.accounts.get(aid)["enabled"]:
                    return
                removed = {k for k, v in self.messages.items() if v["account_id"] == aid} - incoming.keys()
                for key in removed:
                    self.predictions.pop(key, None)
                    self.bodies.pop(key, None)
                self.messages = {k: v for k, v in self.messages.items() if v["account_id"] != aid}
                self.messages.update(incoming)
                self.account_status[aid].update(
                    status="classifying",
                    loaded=len(incoming),
                    truncated=truncated,
                    last_sync=int(time.time() * 1000),
                )
            saved = self.state.read(list(incoming))
            for rec in records:
                if self.stop.is_set() or not self.accounts.get(aid)["enabled"]:
                    break
                key = message_key(aid, rec["provider_id"])
                if rec.get("category") or key in self.predictions or saved.get(key, {}).get("category"):
                    continue
                try:
                    with reader_lock:
                        full = reader.detail(rec["provider_id"])
                    self._cache_body(key, full)
                    with self.model_lock:
                        if self.model is None:
                            self.model = load_service_model(
                                self.cfg.model_ref,
                                models_dir=self.paths.models_dir,
                                device=self.cfg.device,
                                allow_remote_endpoint=False,
                                keep_loaded_minutes=self.cfg.keep_loaded_minutes,
                            )
                        prediction = self.model.predict([full])[0]
                        # Never infer new-sender/contact knowledge across unrelated accounts.
                        result = decide(
                            prediction,
                            known_contact=False,
                            new_address=False,
                            primary=False,
                            cfg=self.cfg.for_model(self.model.manifest),
                        )
                    with self.lock:
                        self.predictions[key] = (result.cls, result.pred.prob)
                        if key in self.messages:
                            self.messages[key].update(
                                category=result.cls, confidence=result.pred.prob, source="model"
                            )
                except Exception as e:  # noqa: BLE001 - keep other accounts usable after model/provider failures
                    with self.lock:
                        self.model_error = (
                            "部分邮件暂未完成本机分类（" + type(e).__name__ + "）；仍可查看和手动分类"
                        )
                    break
            with self.lock:
                self.account_status[aid].update(
                    status="ready" if self.accounts.get(aid)["enabled"] else "paused", error=None
                )
        except Exception as e:  # noqa: BLE001 - account isolation boundary; never expose raw provider errors
            # Never include remote exception strings: subjects/addresses can appear in provider errors.
            with self.lock:
                self.account_status[aid].update(
                    status="error",
                    error=safe_error(e)
                    if isinstance(e, ProviderError)
                    else "同步失败，请检查登录和网络（" + type(e).__name__ + "）",
                )
                self.readers.pop(aid, None)

    def _cache_body(self, key, full):
        with self.lock:
            self.bodies[key] = {
                "body": str(full.get("body_text") or full.get("snippet") or "（没有可显示的文字）")[:262144],
                "url": full.get("url", ""),
            }
            self.bodies.move_to_end(key)
            while len(self.bodies) > 64:
                self.bodies.popitem(last=False)

    def detail(self, key: str) -> dict:
        with self.lock:
            item = self.messages.get(key)
            if item is None:
                raise ValueError("邮件已不在已载入列表，请刷新")
            item = dict(item)
            cached = self.bodies.get(key)
            reader = self.readers.get(item["account_id"])
            reader_lock = self.reader_locks.get(item["account_id"])
        if cached is None:
            if reader is None or reader_lock is None:
                raise ProviderError("此账户暂时离线，请刷新后重试")
            if not reader_lock.acquire(timeout=1):
                raise ProviderError("此账户正在同步，请稍后重试预览")
            try:
                full = reader.detail(item["provider_id"])
            finally:
                reader_lock.release()
            self._cache_body(key, full)
            with self.lock:
                cached = dict(self.bodies[key])
        with self.lock:
            if key not in self.messages:
                self.bodies.pop(key, None)
                raise ValueError("邮件已不在已载入列表，请刷新")
            item = dict(self.messages[key])
        item.pop("provider_id", None)
        saved = self.state.read([key]).get(key, {})
        item["done"] = bool(saved.get("done"))
        if saved.get("category"):
            item.update(category=saved["category"], source="manual")
        return {**item, **cached}

    def action(self, data: dict):
        ids = data.get("ids")
        if not isinstance(ids, list) or not 1 <= len(ids) <= 500 or any(not isinstance(k, str) for k in ids):
            raise ValueError("请选择 1–500 封邮件")
        with self.lock:
            if any(k not in self.messages for k in ids):
                raise ValueError("列表已更新，请重新选择邮件")
            before = self.state.edit(ids, data.get("action"), data.get("category"))
            token = secrets.token_urlsafe(24)
            self.undos[token] = before
            while len(self.undos) > 50:
                self.undos.popitem(last=False)
            return {"ok": True, "undo": token}

    def undo(self, token: str):
        with self.lock:
            before = self.undos.get(token)
            if before is None:
                raise ValueError("撤销已失效，请重新查看邮件")
            self.state.undo(before)
            self.undos.pop(token)
        return {"ok": True}

    def enable(self, account_id: str, enabled: bool):
        self.accounts.enable(account_id, enabled)
        with self.lock:
            self.account_status.setdefault(account_id, {}).update(status="waiting" if enabled else "paused")
        if enabled:
            self.refresh()
        return {"ok": True}

    def connect(self, data: dict):
        name = Accounts.name(data.get("name"))
        provider = data.get("provider")
        if provider not in ("gmail", "gmail-existing", "imap", "microsoft"):
            raise ValueError("请选择邮箱类型")
        reconnect_id = data.get("reconnect_id")
        if reconnect_id:
            existing = self.accounts.get(reconnect_id)
            if existing["provider"] != provider:
                raise ValueError("重新连接时请选择原邮箱类型")
        elif len(self.accounts.all()) >= 32:
            raise ValueError("当前最多连接 32 个账户")
        job = uuid.uuid4().hex
        with self.lock:
            if any(j["status"] == "pending" for j in self.jobs.values()):
                raise ValueError("请先完成当前账户的连接")
            self.jobs = {job: {"id": job, "status": "pending", "message": "正在连接账户"}}
        threading.Thread(target=self._connect, args=(job, name, provider, dict(data)), daemon=True).start()
        return {"ok": True, "job": job}

    def _enroll_verified(self, name, provider, settings, aid, existing):
        candidate = {"id": aid, "provider": provider, "settings": settings}
        reader = self.reader_factory(candidate)
        reader.list(1)
        identity = getattr(reader, "identity", None)
        if not identity:
            raise ProviderError("无法确认邮箱身份，请重新连接")
        if existing and existing.get("identity") and existing["identity"] != identity:
            raise ProviderError("登录的不是原邮箱；其他邮箱请使用添加账户")
        for account in self.accounts.all():
            if (
                account["id"] != aid
                and account["provider"] == provider
                and account.get("identity") == identity
            ):
                raise ProviderError("此邮箱已连接，请使用该账户旁的重新连接")
        if existing:
            self.accounts.replace_settings(aid, name, settings)
        else:
            self.accounts.add(name, provider, settings, account_id=aid)
        self.accounts.bind_identity(aid, identity)

    def _connect(self, job: str, name: str, provider: str, data: dict):
        try:
            existing = self.accounts.get(data["reconnect_id"]) if data.get("reconnect_id") else None
            aid = existing["id"] if existing else uuid.uuid4().hex
            if provider == "gmail-existing":
                self.accounts.enroll_home(name, str(data.get("home", "")))
            elif provider == "imap":
                settings = imap_settings(data)
                if existing and settings != existing["settings"]:
                    raise ValueError("重新连接须使用原 IMAP 服务器和用户名；其他邮箱请添加新账户")
                password = data.pop("password", None)
                if not isinstance(password, str) or not password or len(password) > 2048:
                    raise ValueError("请输入应用专用密码")
                account = {"id": aid, "settings": settings, "provider": "imap"}
                test = ImapReader(account, password=password)
                try:
                    test.list(1)
                finally:
                    test.password = None
                password_store(aid, password)
                password = None
                if existing:
                    self.accounts.replace_settings(aid, name, settings)
                else:
                    self.accounts.add(name, "imap", settings, account_id=aid)
            elif provider == "gmail":
                from google_auth_oauthlib.flow import InstalledAppFlow

                secret = self.paths.client_secret
                if not secret.is_file():
                    raise ValueError(
                        "请先在本机配置 Google Desktop OAuth 客户端；可使用“已有 Gmail 配置”连接"
                    )
                paths = Paths.default(self.root / "accounts" / uuid.uuid4().hex).ensure()
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(secret), ["https://www.googleapis.com/auth/gmail.readonly"]
                )
                credentials = flow.run_local_server(
                    port=0,
                    open_browser=True,
                    timeout_seconds=180,
                    authorization_prompt_message="",
                    success_message="账户已连接，可以关闭此页并返回 Email Town。",
                )
                save_credentials(credentials, paths.token)
                self._enroll_verified(
                    name,
                    "gmail",
                    {"config_dir": str(paths.config_dir), "data_dir": str(paths.data_dir)},
                    aid,
                    existing,
                )
            elif provider == "microsoft":
                client_id = str(
                    data.get("client_id") or (existing or {}).get("settings", {}).get("client_id", "")
                ).strip()
                flow = microsoft_device(client_id)
                verification_url = flow.get("verification_uri", "")
                # Never turn an arbitrary provider response into an authentication link.
                if verification_url not in (
                    "https://microsoft.com/devicelogin",
                    "https://www.microsoft.com/devicelogin",
                ):
                    raise ProviderError("Microsoft 返回了意外登录地址，请重试")
                with self.lock:
                    self.jobs[job].update(
                        message="请在 Microsoft 页面输入代码并授权只读访问",
                        user_code=flow["user_code"],
                        verification_url=verification_url,
                    )
                deadline, interval = (
                    time.monotonic() + min(int(flow["expires_in"]), 900),
                    max(5, int(flow.get("interval", 5))),
                )
                result = {}
                while time.monotonic() < deadline and not self.stop.wait(interval):
                    result = request_json(
                        MICROSOFT_TOKEN,
                        data={
                            "client_id": client_id,
                            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                            "device_code": flow["device_code"],
                        },
                    )
                    if result.get("error") == "slow_down":
                        interval += 5
                    elif result.get("error") != "authorization_pending":
                        break
                token_path = self.root / "accounts" / uuid.uuid4().hex / "microsoft-token.json"
                save_microsoft_token(token_path, result)
                self._enroll_verified(
                    name, "microsoft", {"client_id": client_id, "token_path": str(token_path)}, aid, existing
                )
            with self.lock:
                self.readers.pop(aid, None)
                self.jobs[job] = {"id": job, "status": "complete", "message": "账户已连接，正在载入邮件"}
            self.refresh()
        except Exception as e:  # noqa: BLE001 - async connection jobs report safe errors in the UI
            with self.lock:
                self.readers.pop(aid, None)
                self.jobs[job] = {
                    "id": job,
                    "status": "error",
                    "message": safe_error(e)
                    if isinstance(e, (ProviderError, ValueError))
                    else "连接未完成，请重试（" + type(e).__name__ + "）",
                }
