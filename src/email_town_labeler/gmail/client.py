"""GmailClient: a small retrying, quota-aware wrapper over the Gmail v1 API.

Quota: Gmail meters every call in per-user quota units. `messages.get` (format=full) is the
quota-heavy call here; see docs/configuration.md for the current per-method costs. `history.list`,
`messages.list`, `labels.list` and `getProfile` are cheap. `get_records` is paced by a token
bucket (`max_gets_per_minute`) so a backfill never trips a 429 storm; `get_metadata` (headers only,
cheaper) has its own, faster bucket (`max_metadata_per_minute`).

Logs carry ids, counts and statuses only, never message content.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from collections.abc import Callable, Iterable

from ..types import EmailRecord, HistoryEvent
from .parse import parse_message, parse_metadata

log = logging.getLogger("email_town_labeler.gmail")

ME = "me"
HISTORY_TYPES = ["messageAdded", "labelAdded", "labelRemoved"]
RATE_LIMIT_REASONS = {"rateLimitExceeded", "userRateLimitExceeded"}
API_DISABLED_REASONS = {"accessNotConfigured", "SERVICE_DISABLED"}  # errors[] / details[] spelling
MAX_BATCH = 50  # Gmail accepts 100 per batch but throttles above ~50
MAX_BACKOFF = 64.0


class HistoryExpired(Exception):
    """Gmail returned 404 for startHistoryId: the id is too old; rescan instead."""


def _status(e: BaseException) -> int | None:
    return getattr(getattr(e, "resp", None), "status", None)


def _error_body(e: BaseException) -> dict:
    """The `error` object of an HttpError's JSON body, or {}."""
    try:
        err = json.loads((getattr(e, "content", b"") or b"").decode("utf-8"))["error"]
    except (ValueError, KeyError, TypeError, AttributeError):
        return {}
    return err if isinstance(err, dict) else {}


def _reasons_in(items: object) -> set[str]:
    return {d.get("reason", "") for d in items if isinstance(d, dict)} if isinstance(items, list) else set()


def _reasons(e: BaseException) -> set[str]:
    """`error.errors[].reason` values from an HttpError body (e.g. 'rateLimitExceeded')."""
    return _reasons_in(_error_body(e).get("errors"))


def api_disabled_project(e: BaseException) -> str | None:
    """For the 403 "Gmail API has not been used in project N before or it is disabled": the project
    number ("" if the body names none). None for any other error."""
    if _status(e) != 403:
        return None
    body = _error_body(e)
    if not (_reasons_in(body.get("errors")) | _reasons_in(body.get("details"))) & API_DISABLED_REASONS:
        return None
    m = re.search(r"\bprojects?[ /](\d+)", str(body.get("message", "")))
    return m.group(1) if m else ""


def is_retryable(e: BaseException) -> bool:
    """429, any 5xx, or a 403 whose reason is a rate limit (not a permissions 403)."""
    s = _status(e)
    if s is None:
        return False
    return s == 429 or 500 <= s < 600 or (s == 403 and bool(_reasons(e) & RATE_LIMIT_REASONS))


def _retry_after(e: BaseException) -> float:
    resp = getattr(e, "resp", None)
    try:
        return float(resp.get("retry-after", 0)) if hasattr(resp, "get") else 0.0
    except (TypeError, ValueError):
        return 0.0


class _TokenBucket:
    """`rate_per_min` tokens per minute, burst up to `capacity`. take(n) sleeps until n are free."""

    def __init__(
        self, rate_per_min: float, capacity: float, clock: Callable[[], float], sleep: Callable[[float], None]
    ):
        self.rate = rate_per_min / 60.0
        self.capacity = max(1.0, capacity)
        self.tokens = self.capacity
        self.clock, self.sleep = clock, sleep
        self.stamp = clock()

    def take(self, n: int) -> None:
        n = min(float(n), self.capacity)
        now = self.clock()
        self.tokens = min(self.capacity, self.tokens + (now - self.stamp) * self.rate)
        self.stamp = now
        if self.tokens < n:
            wait = (n - self.tokens) / self.rate
            self.sleep(wait)
            self.stamp = max(self.clock(), now + wait)  # fake sleeps may not move the clock
            self.tokens = n
        self.tokens -= n


class GmailClient:
    """Retrying wrapper. Retries 429 / 5xx / 403 rate limits with exponential backoff + jitter.

    max_gets_per_minute: pace for format=full gets (None or 0 disables pacing).
    max_metadata_per_minute: pace for format=metadata gets (None or 0 disables pacing).
    batch_size: messages per batch request in get_records / get_metadata (capped at 50).
    """

    def __init__(
        self,
        service,
        *,
        max_retries: int = 6,
        sleep: Callable[[float], None] = time.sleep,
        max_gets_per_minute: float | None = 90,
        max_metadata_per_minute: float | None = 240,
        batch_size: int = MAX_BATCH,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.service = service
        self.max_retries = max_retries
        self.sleep = sleep
        self.batch_size = max(1, min(MAX_BATCH, batch_size))

        def bucket(rate: float | None) -> _TokenBucket | None:
            return _TokenBucket(rate, min(self.batch_size, rate), clock, sleep) if rate else None

        self._bucket = bucket(max_gets_per_minute)
        self._meta_bucket = bucket(max_metadata_per_minute)

    @classmethod
    def from_credentials(cls, creds, **kwargs) -> GmailClient:
        from googleapiclient.discovery import build

        return cls(build("gmail", "v1", credentials=creds, cache_discovery=False), **kwargs)

    # -- plumbing --------------------------------------------------------------------------

    def _backoff(self, attempt: int, e: BaseException | None = None) -> float:
        wait = min(MAX_BACKOFF, 2.0**attempt) + random.uniform(0, 1)
        if e is not None:
            wait = max(wait, _retry_after(e))
        return wait

    def _execute(self, req):
        for attempt in range(self.max_retries + 1):
            try:
                return req.execute()
            except Exception as e:  # HttpError, or transport errors without a status
                if not is_retryable(e) or attempt == self.max_retries:
                    raise
                wait = self._backoff(attempt, e)
                log.warning("gmail %s; retry %d/%d in %.1fs", _status(e), attempt + 1, self.max_retries, wait)
                self.sleep(wait)

    @property
    def _users(self):
        return self.service.users()

    # -- API -------------------------------------------------------------------------------

    def profile(self) -> dict:
        """{"emailAddress", "historyId", "messagesTotal", ...}. Cheap."""
        return self._execute(self._users.getProfile(userId=ME))

    def list_message_ids(
        self,
        *,
        query: str | None = None,
        label_ids: list[str] | None = None,
        max_results: int = 100,
        include_spam_trash: bool = False,
    ) -> list[str]:
        """Up to max_results ids, newest first, following nextPageToken. Cheap per page.

        Gmail hides SPAM and TRASH unless include_spam_trash is set."""
        ids: list[str] = []
        token = None
        while len(ids) < max_results:
            kw: dict = {"userId": ME, "maxResults": min(500, max_results - len(ids))}
            if query:
                kw["q"] = query
            if label_ids:
                kw["labelIds"] = list(label_ids)
            if include_spam_trash:
                kw["includeSpamTrash"] = True
            if token:
                kw["pageToken"] = token
            resp = self._execute(self._users.messages().list(**kw))
            ids += [m["id"] for m in resp.get("messages", [])]
            token = resp.get("nextPageToken")
            if not token:
                break
        return ids[:max_results]

    def get_records(self, ids: list[str]) -> list[EmailRecord]:
        """format=full gets, parsed, in input order. Quota-heavy: batched (<=50) and paced.

        404s (deleted since listing) are skipped; rate-limited items are retried with backoff;
        any other error is raised.
        """

        def make(mid: str):
            return self._users.messages().get(userId=ME, id=mid, format="full")

        got, order = self._fetch_all(ids, make, self._bucket, "get_records")
        return [parse_message(got[m]) for m in order if m in got]

    def get_metadata(self, ids: list[str], headers: Iterable[str]) -> list[EmailRecord]:
        """format=metadata gets: id, thread_id, label_ids, internal_date, history_id and only the named
        `headers` (lower-cased keys). No snippet, no body. Batched and paced like get_records, but cheaper."""
        names = list(headers)

        def make(mid: str):
            return self._users.messages().get(userId=ME, id=mid, format="metadata", metadataHeaders=names)

        got, order = self._fetch_all(ids, make, self._meta_bucket, "get_metadata")
        return [parse_metadata(got[m], names) for m in order if m in got]

    def _fetch_all(
        self, ids: list[str], make: Callable[[str], object], bucket: _TokenBucket | None, what: str
    ) -> tuple[dict[str, dict], list[str]]:
        order = list(dict.fromkeys(ids))  # dedupe, keep order
        got: dict[str, dict] = {}
        for i in range(0, len(order), self.batch_size):
            self._fetch_chunk(order[i : i + self.batch_size], got, make, bucket, what)
        missing = len(order) - len(got)
        if missing:
            log.info("%s: %d of %d ids not found", what, missing, len(order))
        return got, order

    def _fetch_chunk(
        self,
        chunk: list[str],
        got: dict[str, dict],
        make: Callable[[str], object],
        bucket: _TokenBucket | None,
        what: str,
    ) -> None:
        pending = chunk
        for attempt in range(self.max_retries + 1):
            if bucket:
                bucket.take(len(pending))
            retry: list[str] = []
            errors: list[BaseException] = []
            fatal: list[BaseException] = []

            def on_result(request_id, response, exception, retry=retry, errors=errors, fatal=fatal):
                if exception is None:
                    got[request_id] = response
                elif _status(exception) == 404:
                    pass
                elif is_retryable(exception):
                    retry.append(request_id)
                    errors.append(exception)
                else:
                    fatal.append(exception)

            batch = self.service.new_batch_http_request(callback=on_result)
            for mid in pending:
                batch.add(make(mid), request_id=mid)
            try:
                batch.execute()
            except Exception as e:  # whole batch failed
                if not is_retryable(e) or attempt == self.max_retries:
                    raise
                retry, errors = list(pending), [e]
            if fatal:
                raise fatal[0]
            if not retry:
                return
            if attempt == self.max_retries:
                raise errors[-1]
            wait = self._backoff(attempt, errors[-1])
            log.warning("%s: %d of %d throttled; retry in %.1fs", what, len(retry), len(pending), wait)
            self.sleep(wait)
            pending = retry

    def get_label_ids(self, id: str) -> list[str]:
        """Current labels of one message (format=minimal, much cheaper than full)."""
        return list(
            self._execute(self._users.messages().get(userId=ME, id=id, format="minimal")).get("labelIds", [])
        )

    def list_labels(self) -> list[dict]:
        """[{"id", "name", "type"[, "color"]}] for every system and user label. Cheap.

        "color" ({"textColor", "backgroundColor"}) is present only on labels that have one.
        """
        resp = self._execute(self._users.labels().list(userId=ME))
        out = []
        for lb in resp.get("labels", []):
            row = {"id": lb["id"], "name": lb["name"], "type": lb.get("type", "user")}
            if lb.get("color"):
                row["color"] = dict(lb["color"])
            out.append(row)
        return out

    def create_label(self, name: str, color: dict[str, str] | None = None) -> dict:
        """Create a visible user label, optionally colored ({"textColor", "backgroundColor"}, Gmail
        palette values only). A 409 (exists) or 400 (e.g. a color outside the palette) HttpError
        propagates to the caller."""
        body: dict = {"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"}
        if color:
            body["color"] = dict(color)
        return self._execute(self._users.labels().create(userId=ME, body=body))

    def set_label_color(self, id: str, color: dict[str, str]) -> dict:
        """labels.patch the color of one user label. A 400 (color outside the palette) propagates."""
        return self._execute(self._users.labels().patch(userId=ME, id=id, body={"color": dict(color)}))

    def rename_label(self, id: str, name: str) -> dict:
        """labels.patch the name of one user label. Messages keep it (the id does not change). A 409
        (the name is taken, compared case-insensitively) propagates."""
        return self._execute(self._users.labels().patch(userId=ME, id=id, body={"name": name}))

    def modify(self, id: str, *, add: Iterable[str] = (), remove: Iterable[str] = ()) -> None:
        """Add/remove label ids on one message. No call when both are empty."""
        add, remove = list(add), list(remove)
        if not add and not remove:
            return
        body = {"addLabelIds": add, "removeLabelIds": remove}
        self._execute(self._users.messages().modify(userId=ME, id=id, body=body))

    def history(self, start_history_id: str) -> tuple[list[HistoryEvent], str]:
        """All history since start_history_id -> (events in order, latest historyId). Cheap per page.

        Raises HistoryExpired when Gmail 404s the start id (typically older than ~a week).
        """
        events: list[HistoryEvent] = []
        token, latest = None, str(start_history_id)
        while True:
            kw: dict = {
                "userId": ME,
                "startHistoryId": str(start_history_id),
                "historyTypes": HISTORY_TYPES,
                "maxResults": 500,
            }
            if token:
                kw["pageToken"] = token
            try:
                resp = self._execute(self._users.history().list(**kw))
            except Exception as e:
                if _status(e) == 404:
                    raise HistoryExpired(str(start_history_id)) from e
                raise
            for rec in resp.get("history", []):
                events += _events(rec)
            latest = str(resp.get("historyId") or latest)
            token = resp.get("nextPageToken")
            if not token:
                return events, latest


def _events(rec: dict) -> list[HistoryEvent]:
    out: list[HistoryEvent] = []
    for item in rec.get("messagesAdded", []):
        m = item["message"]
        lids = list(m.get("labelIds", []))
        out.append(HistoryEvent("added", m["id"], lids, list(lids)))
    for key, kind in (("labelsAdded", "label_added"), ("labelsRemoved", "label_removed")):
        for item in rec.get(key, []):
            m = item["message"]
            out.append(
                HistoryEvent(kind, m["id"], list(item.get("labelIds", [])), list(m.get("labelIds", [])))
            )
    return out
