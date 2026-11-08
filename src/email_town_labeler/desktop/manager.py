"""Local workbench coordinator: bounded background reads, one shared local model, isolated workflow state."""

from __future__ import annotations

import hashlib
import json
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
from .images import body_images, valid_image_bytes
from .mail import FOLDERS, MAIL_ACTIONS, MailStore, compose_data, recipient_headers
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
    "action_needed": "Action needed",
    "new_sender": "New sender",
    "newsletters": "Newsletters",
    "orders": "Orders & bills",
    "promotional": "Promotions",
    "travel": "Travel",
    "updates": "Updates",
    "unknown": "Unclassified",
}


# Read-ahead: the newest bodies per account are fetched after the list so the first opens are instant.
# A reader can lower it with a `read_ahead` attribute (Gmail does: bodies cost quota, opens are fast).
PREFETCH = 10
# The model triages received mail. Your own Sent mail is not classified: on Gmail a Sent folder
# of 100 would read 100 bodies at 60 quota units each, a whole minute of the per-user budget.
CLASSIFIED_FOLDERS = frozenset({"inbox", "archive", "trash"})
# Bodies per provider round trip; the account lock is released between chunks so a preview can run.
BODY_CHUNK = 10
BODY_CACHE = 128
# A preview waits this long for a busy account instead of failing while the list syncs.
PREVIEW_WAIT = 20
# Longest a /api/state long-poll is held before it answers "unchanged".
STATE_WAIT = 10.0


def safe_error(exc: Exception) -> str:
    if isinstance(exc, (ProviderError, ValueError)):
        # Our own ValueErrors contain no remote mail content. Callers never pass parse errors through.
        return str(exc)[:240]
    return "Connection unavailable. Check your network or reconnect the account (" + type(exc).__name__ + ")"


class Workbench:
    def __init__(self, root: Path, default_paths: Paths, *, reader_factory=create_reader, classifier=None):
        self.root, self.paths = root, default_paths
        self.accounts = Accounts(root, default_paths)
        self.state = LocalState(root / "workflow.sqlite")
        self.mail_store = MailStore(root / "mail")
        self.mail_slots = threading.BoundedSemaphore(3)
        self.folder = "inbox"
        self.epochs = {}
        self.refresh_again = False
        self.reader_factory, self.model = reader_factory, classifier
        self.cfg = load_config(default_paths.config_file)
        self.lock, self.model_lock = threading.RLock(), threading.RLock()
        # Woken on every visible change so a waiting /api/state answers at once.
        self.changed, self.version, self.seq = threading.Condition(self.lock), 0, 0
        self.reader_locks, self.previews_waiting = {}, {}
        self.folder_rows = {}
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
        self._touch()
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
                    for k in (
                        "host",
                        "port",
                        "username",
                        "client_id",
                        "smtp_host",
                        "smtp_port",
                        "smtp_security",
                    )
                    if k in a["settings"]
                }
                accounts.append(
                    {k: a[k] for k in ("id", "name", "provider", "enabled")}
                    | status
                    | {
                        "connection": connection,
                        "capabilities": self.account_capabilities(a["id"]),
                        "address": getattr(self.readers.get(a["id"]), "address", ""),
                    }
                )
            result = {
                "accounts": accounts,
                "messages": sorted(rows, key=lambda m: m["date"], reverse=True),
                "categories": [{"id": c, "name": CATEGORY_NAMES[c]} for c in LABEL_CLASSES],
                "refreshing": self.refreshing,
                "last_refresh": self.last_refresh,
                "jobs": list(self.jobs.values()),
                "demo": self.demo,
                "model_error": self.model_error,
                "limit": self.limit,
                "folder": self.folder,
            }
            self.seq += 1
            digest = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode())
            # seq orders responses that overlap in the browser; etag says whether anything changed.
            return result | {"etag": digest.hexdigest()[:32], "seq": self.seq, "version": self.version}

    def _touch(self):
        with self.lock:
            self.version += 1
            self.changed.notify_all()

    def wait_snapshot(self, etag: str, timeout: float = STATE_WAIT) -> dict:
        """Answer as soon as the snapshot differs from `etag`, or say "unchanged" after `timeout`."""
        deadline = time.monotonic() + timeout
        while True:
            snap = self.snapshot()
            remaining = deadline - time.monotonic()
            if snap["etag"] != etag or remaining <= 0 or self.stop.is_set():
                return snap if snap["etag"] != etag else {"unchanged": True, "etag": etag, "seq": snap["seq"]}
            with self.lock:
                if self.version == snap["version"]:
                    # Local edits and clock-only changes need no notify: re-check at least twice a second.
                    self.changed.wait(min(0.5, remaining))

    def refresh(self, *, more=False):
        with self.lock:
            if more:
                self.limit = min(1000, self.limit + 100)
            if self.stop.is_set():
                return
            if self.refreshing:
                self.refresh_again = True
                return
            self.refreshing = True
            self.model_error = None
            self._touch()
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
                again, self.refresh_again = self.refresh_again, False
                self._touch()
            if again and not self.stop.is_set():
                self.refresh()

    def _account(self, account):
        aid = account["id"]
        with self.lock:
            folder, epoch = self.folder, self.epochs.get(aid, 0)
            self.account_status.setdefault(aid, {"loaded": 0, "last_sync": None, "truncated": False})
            self.account_status[aid].update(status="syncing", error=None)
            reader_lock = self.reader_locks.setdefault(aid, threading.RLock())
            self._touch()
        try:
            with reader_lock:
                reader = self.readers.get(aid)
                if reader is None:
                    reader = self.reader_factory(account)
                    self.readers[aid] = reader
                kwargs = {} if folder == "inbox" else {"folder": folder}
                if getattr(reader, "progressive", False):
                    kwargs["progress"] = lambda partial: self._show_partial(aid, partial, folder, epoch)
                records, truncated = reader.list(self.limit, **kwargs)
                if getattr(reader, "identity", None):
                    try:
                        self.accounts.bind_identity(aid, reader.identity)
                    except ValueError as exc:
                        raise ProviderError(str(exc)) from exc
            incoming = self._rows(aid, records, folder)
            with self.lock:
                if (
                    not self.accounts.get(aid)["enabled"]
                    or folder != self.folder
                    or epoch != self.epochs.get(aid, 0)
                ):
                    self.refresh_again = True
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
                self._touch()
            saved = self.state.read(list(incoming))
            self._read_ahead(aid, account, reader, reader_lock, records, saved, folder, epoch)
            with self.lock:
                self.account_status[aid].update(
                    status="ready" if self.accounts.get(aid)["enabled"] else "paused", error=None
                )
                self._touch()
        except Exception as e:  # noqa: BLE001 - account isolation boundary; never expose raw provider errors
            # Never include remote exception strings: subjects/addresses can appear in provider errors.
            with self.lock:
                self.account_status[aid].update(
                    status="error",
                    error=safe_error(e)
                    if isinstance(e, ProviderError)
                    else "Sync failed. Check your sign-in and network (" + type(e).__name__ + ")",
                )
                self.readers.pop(aid, None)
                self._touch()

    def _rows(self, aid, records, folder) -> dict:
        incoming = {}
        for rec in records:
            key = message_key(aid, rec["provider_id"])
            category = rec.get("category")
            prediction = self.predictions.get(key)
            source = (
                "mailbox"
                if category
                else "model"
                if prediction
                else "none"
                if folder not in CLASSIFIED_FOLDERS
                else "pending"
            )
            if not category:
                category = prediction[0] if prediction else "unknown"
            incoming[key] = {
                "id": key,
                "account_id": aid,
                "provider_id": rec["provider_id"],
                "subject": rec.get("headers", {}).get("subject", "")[:2000] or "(No subject)",
                "sender": rec.get("headers", {}).get("from", "")[:1000],
                "date": rec.get("internal_date", 0),
                "category": category,
                "done": False,
                "unread": "UNREAD" in rec.get("label_ids", []),
                "starred": "STARRED" in rec.get("label_ids", []),
                "source": source,
                "confidence": prediction[1] if prediction else None,
            }
        return incoming

    def _show_partial(self, aid, records, folder, epoch):
        """Rows a long list has already read appear before the rest; the final list replaces them."""
        incoming = self._rows(aid, records, folder)
        with self.lock:
            if folder == self.folder and epoch == self.epochs.get(aid, 0) and not self.stop.is_set():
                self.messages.update(incoming)
                self._touch()

    def _read_ahead(self, aid, account, reader, reader_lock, records, saved, folder, epoch):
        """Fetch bodies the model needs plus the newest few, in chunks, and classify each chunk at once."""
        keys = {rec["provider_id"]: message_key(aid, rec["provider_id"]) for rec in records}
        unlabelled = {
            rec["provider_id"]
            for rec in records
            if folder in CLASSIFIED_FOLDERS
            and not rec.get("category")
            and keys[rec["provider_id"]] not in self.predictions
            and not saved.get(keys[rec["provider_id"]], {}).get("category")
        }
        with self.lock:
            cached = set(self.bodies)
        newest = {rec["provider_id"] for rec in records[: getattr(reader, "read_ahead", PREFETCH)]}
        wanted = [
            rec["provider_id"]
            for rec in records
            if rec["provider_id"] in unlabelled
            or (rec["provider_id"] in newest and keys[rec["provider_id"]] not in cached)
        ]
        for start in range(0, len(wanted), BODY_CHUNK):
            if (
                self.stop.is_set()
                or not self.accounts.get(aid)["enabled"]
                or folder != self.folder
                or epoch != self.epochs.get(aid, 0)
            ):
                return
            chunk = wanted[start : start + BODY_CHUNK]
            self._yield_to_previews(aid)
            try:
                with reader_lock:
                    if hasattr(reader, "details"):
                        fulls = reader.details(chunk)
                    else:
                        fulls = {pid: reader.detail(pid) for pid in chunk}
                for pid, full in fulls.items():
                    self._cache_body(keys[pid], full)
                self._classify(
                    [(keys[pid], fulls[pid]) for pid in chunk if pid in unlabelled and pid in fulls]
                )
            except Exception as e:  # noqa: BLE001 - keep other accounts usable after model/provider failures
                with self.lock:
                    self.model_error = (
                        "Some messages could not be classified locally ("
                        + type(e).__name__
                        + "); you can still view and classify them manually"
                    )
                    self._touch()
                return

    def _classify(self, items):
        if not items:
            return
        with self.model_lock:
            if self.model is None:
                self.model = load_service_model(
                    self.cfg.model_ref,
                    models_dir=self.paths.models_dir,
                    device=self.cfg.device,
                    allow_remote_endpoint=False,
                    keep_loaded_minutes=self.cfg.keep_loaded_minutes,
                )
            predictions = self.model.predict([full for _key, full in items])
            # Never infer new-sender/contact knowledge across unrelated accounts.
            results = [
                decide(
                    prediction,
                    known_contact=False,
                    new_address=False,
                    primary=False,
                    cfg=self.cfg.for_model(self.model.manifest),
                )
                for prediction in predictions
            ]
        with self.lock:
            for (key, _full), result in zip(items, results, strict=True):
                self.predictions[key] = (result.cls, result.pred.prob)
                if key in self.messages:
                    self.messages[key].update(
                        category=result.cls, confidence=result.pred.prob, source="model"
                    )
            self._touch()

    def _yield_to_previews(self, aid):
        # Background reads step aside while someone waits to open a message on this account.
        deadline = time.monotonic() + PREVIEW_WAIT
        while self.previews_waiting.get(aid) and time.monotonic() < deadline and not self.stop.is_set():
            time.sleep(0.02)

    def _cache_body(self, key, full):
        with self.lock:
            self.bodies[key] = {
                "body": str(full.get("body_text") or full.get("snippet") or "(No text available)")[:262144],
                "images": body_images(full.get("body_html"), full.get("attachments", [])),
                "url": full.get("url", ""),
                "headers": full.get("headers", {}),
                "recipients": recipient_headers(full.get("headers", {})),
                "attachments": full.get("attachments", []),
                "thread_id": full.get("thread_id", ""),
            }
            self.bodies.move_to_end(key)
            while len(self.bodies) > BODY_CACHE:
                self.bodies.popitem(last=False)

    def detail(self, key: str) -> dict:
        with self.lock:
            item = self.messages.get(key)
            if item is None:
                raise ValueError("Message is no longer in the loaded list. Refresh and try again.")
            item = dict(item)
            cached = self.bodies.get(key)
            reader = self.readers.get(item["account_id"])
            reader_lock = self.reader_locks.get(item["account_id"])
        if cached is None:
            if reader is None or reader_lock is None:
                raise ProviderError("This account is offline. Refresh and try again.")
            aid = item["account_id"]
            with self.lock:
                self.previews_waiting[aid] = self.previews_waiting.get(aid, 0) + 1
            try:
                acquired = reader_lock.acquire(timeout=PREVIEW_WAIT)
            finally:
                with self.lock:
                    self.previews_waiting[aid] -= 1
            if not acquired:
                raise ProviderError("This account is still syncing. Try the preview again shortly.")
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
                raise ValueError("Message is no longer in the loaded list. Refresh and try again.")
            item = dict(self.messages[key])
        item.pop("provider_id", None)
        saved = self.state.read([key]).get(key, {})
        item["done"] = bool(saved.get("done"))
        if saved.get("category"):
            item.update(category=saved["category"], source="manual")
        return {**item, **cached}

    def account_capabilities(self, aid):
        reader = self.readers.get(aid)
        if reader is None or not hasattr(reader, "capabilities"):
            return {
                "send": False,
                "write": False,
                "attachments": False,
                "max_attachment_bytes": 0,
                "reason": "Wait for the account to connect",
            }
        try:
            return reader.capabilities()
        except Exception:  # noqa: BLE001 - never expose credential data
            return {
                "send": False,
                "write": False,
                "attachments": False,
                "max_attachment_bytes": 0,
                "reason": "Reconnect this account",
            }

    def change_folder(self, folder):
        if folder not in FOLDERS:
            raise ValueError("Unknown mail folder")
        with self.lock:
            if folder != self.folder:
                # Show the folder's last list at once and revalidate it, instead of an empty list.
                self.folder_rows[self.folder] = self.messages
                self.folder = folder
                self.messages = self.folder_rows.pop(folder, {})
                for aid in self.readers:
                    self.epochs[aid] = self.epochs.get(aid, 0) + 1
                self._touch()
        self.refresh()
        return {"ok": True}

    def attachment(self, key, attachment_id):
        with self.lock:
            item = dict(self.messages.get(key) or {})
            if not item:
                raise ValueError("Message is no longer in the loaded list")
            aid = item["account_id"]
            lock = self.reader_locks.get(aid)
        if not lock or not lock.acquire(timeout=1):
            raise ProviderError("Account is busy. Try again shortly")
        try:
            reader = self.readers.get(aid)
            if not reader or not self.accounts.get(aid)["enabled"]:
                raise ProviderError("Account is offline or paused")
            return reader.attachment(item["provider_id"], attachment_id)
        finally:
            lock.release()

    def image(self, key, attachment_id):
        detail = self.detail(key)
        image = next((item for item in detail["images"] if item.get("attachment_id") == attachment_id), None)
        if not image:
            raise ValueError("This image is not available in the message")
        _meta, raw = self.attachment(key, attachment_id)
        if not valid_image_bytes(image["type"], raw):
            raise ValueError("Image format is unsupported or exceeds 8 MB. Download the attachment instead")
        return image["type"], raw

    def mail_action(self, data):
        action, ids = data.get("action"), data.get("ids")
        if action not in MAIL_ACTIONS:
            raise ValueError("Unknown mailbox action")
        if not isinstance(ids, list) or not 1 <= len(ids) <= 500 or any(not isinstance(k, str) for k in ids):
            raise ValueError("Select 1–500 loaded messages")
        ids = list(dict.fromkeys(ids))
        request_id = data.get("request_id")
        payload = {"action": action, "ids": ids}
        # Return the durable receipt before rechecking a list which a completed action changed.
        try:
            self.mail_store.receipt(request_id)
        except ValueError:
            pass
        else:
            self.mail_store.reserve(request_id, payload)
            return {"job_id": request_id}
        with self.lock:
            if any(k not in self.messages for k in ids):
                raise ValueError("The list changed. Select messages again")
            selected = [dict(self.messages[k]) for k in ids]
            for item in selected:
                aid = item["account_id"]
                if not self.accounts.get(aid)["enabled"] or not self.account_capabilities(aid)["write"]:
                    raise ValueError("A selected account is paused or needs mail-management permission")
        return self._mail_job(
            request_id, payload, lambda: self._mutate_messages(selected, action, request_id)
        )

    def _mail_job(self, request_id, payload, work):
        if not self.mail_slots.acquire(blocking=False):
            raise ValueError("Three mail operations are already running. Wait for one to finish")
        try:
            if not self.mail_store.reserve(request_id, payload):
                self.mail_slots.release()
                return {"job_id": request_id}
        except BaseException:
            self.mail_slots.release()
            raise

        def run():
            try:
                result = work()
                self.mail_store.finish(request_id, result)
            except Exception:  # noqa: BLE001 - a network failure may follow remote acceptance
                self.mail_store.finish(
                    request_id,
                    {
                        "status": "unknown",
                        "succeeded": [],
                        "failed": [],
                        "message": "Could not confirm the result. Check Sent or the original mailbox before trying a new request.",
                    },
                )
            finally:
                self.mail_slots.release()
                self.refresh()

        threading.Thread(target=run, name="workbench-mail-operation", daemon=True).start()
        return {"job_id": request_id}

    def _mutate_messages(self, selected, action, request_id):
        result = {"status": "pending", "succeeded": [], "failed": [], "message": "Working…"}
        for item in selected:
            aid, key = item["account_id"], item["id"]
            try:
                with self.reader_locks[aid]:
                    reader = self.readers.get(aid)
                    if not reader or not self.accounts.get(aid)["enabled"]:
                        raise ProviderError("Account is offline or paused")
                    reader.mutate(item["provider_id"], action)
                    with self.lock:
                        self.epochs[aid] = self.epochs.get(aid, 0) + 1
                        self.bodies.pop(key, None)
                        if action in ("trash", "archive", "restore"):
                            self.messages.pop(key, None)
                        elif key in self.messages:
                            field = "unread" if action in ("read", "unread") else "starred"
                            self.messages[key][field] = action in ("unread", "star")
                        self._touch()
                result["succeeded"].append(key)
            except Exception as exc:  # noqa: BLE001 - preserve successes across independent accounts
                result["failed"].append(
                    {
                        "id": key,
                        "error": safe_error(exc)
                        if isinstance(exc, ProviderError)
                        else "Result not confirmed. Refresh the original mailbox before retrying.",
                    }
                )
            self.mail_store.finish(request_id, result)
        result["status"] = (
            "partial"
            if result["failed"] and result["succeeded"]
            else "failed"
            if result["failed"]
            else "complete"
        )
        result["message"] = (
            f"{len(result['succeeded'])} updated in the original mailboxes; {len(result['failed'])} failed or unconfirmed."
        )
        return result

    def send_mail(self, data):
        clean = compose_data(data)
        aid, request_id = clean["account_id"], data.get("request_id")
        self.accounts.get(aid)
        payload = {"send": clean, "draft_id": data.get("draft_id")}
        try:
            self.mail_store.receipt(request_id)
        except ValueError:
            pass
        else:
            self.mail_store.reserve(request_id, payload)
            return {"job_id": request_id}
        with self.lock:
            caps = self.account_capabilities(aid)
            if not self.accounts.get(aid)["enabled"] or not caps["send"]:
                raise ValueError(caps["reason"] or "Account is paused")
            compose_data(clean, max_bytes=caps["max_attachment_bytes"])
            if clean["reply_to_id"]:
                item = self.messages.get(clean["reply_to_id"])
                if not item or item["account_id"] != aid:
                    raise ValueError("Reply must use the account that received this message")
                reply = dict(item)
            else:
                reply = None
        draft_id = data.get("draft_id")
        if draft_id:
            draft = self.mail_store.draft(draft_id)
            if draft["account_id"] != aid:
                raise ValueError("Draft belongs to another account")

        def work():
            with self.reader_locks[aid]:
                reader = self.readers.get(aid)
                if not reader or not self.accounts.get(aid)["enabled"]:
                    return {
                        "status": "failed",
                        "succeeded": [],
                        "failed": [],
                        "message": "Account is offline or paused. Draft retained.",
                    }
                original = reader.detail(reply["provider_id"]) if reply else None
                if original is not None:
                    original["provider_id"] = reply["provider_id"]
                result = reader.send(clean, original)
            # Draft removal failure must never turn a successful send into an invitation to retry.
            status = result.get("status", "complete")
            if draft_id and status == "complete":
                try:
                    self.mail_store.delete_matching_draft(draft_id, clean)
                except Exception:  # noqa: BLE001 - accepted delivery must not invite a resend
                    result["message"] = (
                        result.get("message", "Accepted for delivery") + " Local draft was retained."
                    )
            return {
                "status": status,
                "succeeded": result.get("accepted", [request_id]),
                "failed": [
                    {
                        "id": address,
                        "error": "SMTP rejected this recipient. Other recipients may have received the message; resend only to rejected recipients.",
                    }
                    for address in result.get("refused", [])
                ],
                "message": result.get("message", "Accepted for delivery"),
            }

        return self._mail_job(request_id, payload, work)

    def action(self, data: dict):
        ids = data.get("ids")
        if not isinstance(ids, list) or not 1 <= len(ids) <= 500 or any(not isinstance(k, str) for k in ids):
            raise ValueError("Select 1–500 messages")
        with self.lock:
            if any(k not in self.messages for k in ids):
                raise ValueError("The list changed. Select the messages again.")
            before = self.state.edit(ids, data.get("action"), data.get("category"))
            token = secrets.token_urlsafe(24)
            self.undos[token] = before
            while len(self.undos) > 50:
                self.undos.popitem(last=False)
            self._touch()
            return {"ok": True, "undo": token}

    def undo(self, token: str):
        with self.lock:
            before = self.undos.get(token)
            if before is None:
                raise ValueError("Undo expired. Review the messages again.")
            self.state.undo(before)
            self.undos.pop(token)
            self._touch()
        return {"ok": True}

    def enable(self, account_id: str, enabled: bool):
        self.accounts.enable(account_id, enabled)
        with self.lock:
            self.account_status.setdefault(account_id, {}).update(status="waiting" if enabled else "paused")
            self._touch()
        if enabled:
            self.refresh()
        return {"ok": True}

    def connect(self, data: dict):
        name = Accounts.name(data.get("name"))
        provider = data.get("provider")
        if provider not in ("gmail", "gmail-existing", "imap", "microsoft"):
            raise ValueError("Choose an email provider")
        reconnect_id = data.get("reconnect_id")
        if reconnect_id:
            existing = self.accounts.get(reconnect_id)
            if existing["provider"] != provider:
                raise ValueError("Choose the original provider when reconnecting")
        elif len(self.accounts.all()) >= 32:
            raise ValueError("You can connect up to 32 accounts")
        job = uuid.uuid4().hex
        with self.lock:
            if any(j["status"] == "pending" for j in self.jobs.values()):
                raise ValueError("Finish connecting the current account first")
            self.jobs = {job: {"id": job, "status": "pending", "message": "Connecting account"}}
        threading.Thread(target=self._connect, args=(job, name, provider, dict(data)), daemon=True).start()
        return {"ok": True, "job": job}

    def _enroll_verified(self, name, provider, settings, aid, existing):
        candidate = {"id": aid, "provider": provider, "settings": settings}
        reader = self.reader_factory(candidate)
        reader.list(1)
        identity = getattr(reader, "identity", None)
        if not identity:
            raise ProviderError("Could not verify the mailbox identity. Reconnect the account.")
        if existing and existing.get("identity") and existing["identity"] != identity:
            raise ProviderError("This is not the original mailbox. Use Add account for another mailbox.")
        for account in self.accounts.all():
            if (
                account["id"] != aid
                and account["provider"] == provider
                and account.get("identity") == identity
            ):
                raise ProviderError("This mailbox is already connected. Use Reconnect beside that account.")
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
                if existing and any(
                    settings[k] != existing["settings"][k] for k in ("host", "port", "username")
                ):
                    raise ValueError(
                        "Reconnect requires the original IMAP server and username. Use Add account for another mailbox."
                    )
                password = data.pop("password", None)
                if not isinstance(password, str) or not password or len(password) > 2048:
                    raise ValueError("Enter an app-specific password")
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
                        "Configure a Google Desktop OAuth client on this device first, or connect an existing Gmail configuration"
                    )
                paths = Paths.default(self.root / "accounts" / uuid.uuid4().hex).ensure()
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(secret), ["https://www.googleapis.com/auth/gmail.modify"]
                )
                credentials = flow.run_local_server(
                    port=0,
                    open_browser=True,
                    timeout_seconds=180,
                    authorization_prompt_message="",
                    success_message="Account connected. Close this page and return to Email Town.",
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
                    raise ProviderError("Microsoft returned an unexpected sign-in URL. Try again.")
                with self.lock:
                    self.jobs[job].update(
                        message="Enter the code on the Microsoft page and grant permission to read, send and manage mail",
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
                self.jobs[job] = {
                    "id": job,
                    "status": "complete",
                    "message": "Account connected. Loading mail",
                }
            self.refresh()
        except Exception as e:  # noqa: BLE001 - async connection jobs report safe errors in the UI
            with self.lock:
                self.readers.pop(aid, None)
                self.jobs[job] = {
                    "id": job,
                    "status": "error",
                    "message": safe_error(e)
                    if isinstance(e, (ProviderError, ValueError))
                    else "Connection did not complete. Try again (" + type(e).__name__ + ")",
                }
