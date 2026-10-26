"""In-memory, googleapiclient-shaped Gmail service for tests. No network, no credentials.

`FakeGmail()` stands in for `googleapiclient.discovery.build("gmail", "v1", ...)`. Pass it straight
to `GmailClient(FakeGmail())`. It mirrors the call shape (`svc.users().messages().get(...)
.execute()`), the response shapes, and the error behaviour (real `HttpError`s with Gmail-style JSON
bodies) of the methods email-town-labeler uses:

    users().getProfile(userId)
    users().messages().list(userId, q, labelIds, maxResults, pageToken, includeSpamTrash) + list_next
    users().messages().get(userId, id, format=full|minimal|metadata|raw, metadataHeaders, fields)
    users().messages().modify(userId, id, body={addLabelIds, removeLabelIds})
    users().messages().batchModify(userId, body={ids, addLabelIds, removeLabelIds})
    users().labels().list/get/create/patch(userId, ...)  (create/patch accept body.color)
    users().history().list(userId, startHistoryId, historyTypes, labelId, pageToken, maxResults) + list_next
    new_batch_http_request(callback) -> .add(request, callback=None, request_id=None) / .execute()

Semantics worth knowing:
- Every mailbox change (deliver, modify, user_relabel, remove_message) bumps a monotonically
  increasing `history_id` and appends a history record (messagesAdded / labelsAdded /
  labelsRemoved / messagesDeleted). Message stubs in a record carry the labels *after* that event.
- history.list 404s when startHistoryId is older than the retained window (`expire_history()`).
- Responses omit empty lists (`messages`, `history`) exactly like Gmail.
- Unknown request kwargs raise TypeError, as the discovery-based client does.
- `fields=` is accepted and ignored (full resources are returned).
- `q` supports a small subset of Gmail search: in:/label:/category:/is:unread/is:starred,
  newer_than:/older_than: (d/m/y), after:/before: (YYYY/MM/DD or epoch secs), from:, to:, subject:,
  leading `-` negation, and bare words (substring of subject/from/snippet). Anything else raises
  NotImplementedError so a test never silently gets the wrong answer.

Test helpers (none of these consume injected failures or appear in `calls`):

    g = FakeGmail()
    mid = g.deliver(subject="Your order shipped", body="Tracking 123", from_="Shop <s@shop.example>",
                    headers={"List-Unsubscribe": "<mailto:u@shop.example>"}, label_ids=["INBOX", "UNREAD"])
    g.deliver(b"From: a@example.test\\r\\nSubject: raw\\r\\n\\r\\nhi\\r\\n")   # raw RFC 822 bytes/str
    g.deliver(label_ids=["SENT"], to="Pal <pal@example.test>", bcc="x@example.test")  # mail you sent
    g.user_relabel(mid, add=["STARRED"], remove=["email-town-labeler/orders"])  # ids or label names
    g.expire_history()                     # every older startHistoryId now 404s
    g.fail_next(2, 429)                    # next 2 API executions fail (batch items count singly)
    g.fail_next(1, 500, method="history.list")
    g.fail_next(1, 503, method="batch")    # the whole batch HTTP call fails
    g.remove_message(mid)                  # later gets 404
    g.add_label("email-town-labeler/orders")        # create a label behind the client's back (races)
    g.labels_of(mid), g.label_id("email-town-labeler"), g.calls, g.batches, g.history_id

deliver() simple fields: subject, body (text/plain), html (text/html), from_ (or "from"), to, cc, bcc,
headers (extra dict), charset, calendar (True or ICS text: adds a text/calendar alternative and an
invite.ics attachment), attachments [(filename, mime, bytes)]. A field set to None omits that header.
Bodies are encoded exactly as Gmail returns them: real MIME built with the stdlib `email` package,
then each leaf's decoded bytes as unpadded base64url in `body.data` (attachments get
`body.attachmentId` instead of data).
"""

from __future__ import annotations

import base64
import copy
import http
import json
import re
import shlex
import time
from dataclasses import dataclass, field
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

import httplib2
from googleapiclient.errors import BatchError, HttpError

SYSTEM_LABELS = [
    "INBOX",
    "SENT",
    "DRAFT",
    "SPAM",
    "TRASH",
    "UNREAD",
    "STARRED",
    "IMPORTANT",
    "CHAT",
    "CATEGORY_PERSONAL",
    "CATEGORY_SOCIAL",
    "CATEGORY_PROMOTIONS",
    "CATEGORY_UPDATES",
    "CATEGORY_FORUMS",
]
IMMUTABLE_LABELS = {"SENT", "DRAFT", "CHAT"}  # Gmail rejects adding/removing these via modify
MAX_BATCH = 100  # Gmail's server-side limit per batch request
_HISTORY_KEYS = {
    "messageAdded": "messagesAdded",
    "messageDeleted": "messagesDeleted",
    "labelAdded": "labelsAdded",
    "labelRemoved": "labelsRemoved",
}
_ERRORS = {  # status -> (google status, reason, message), as Gmail sends them
    400: ("INVALID_ARGUMENT", "invalidArgument", "Invalid argument"),
    403: ("PERMISSION_DENIED", "userRateLimitExceeded", "User-rate limit exceeded."),
    404: ("NOT_FOUND", "notFound", "Requested entity was not found."),
    409: ("ALREADY_EXISTS", "alreadyExists", "Label name exists or conflicts"),
    429: ("RESOURCE_EXHAUSTED", "rateLimitExceeded", "Too many concurrent requests for user."),
    500: ("INTERNAL", "backendError", "Backend Error"),
    503: ("UNAVAILABLE", "backendError", "The service is currently unavailable."),
}


def http_error(
    status: int,
    message: str | None = None,
    *,
    reason: str | None = None,
    retry_after: float | None = None,
    path: str = "",
) -> HttpError:
    """A real googleapiclient HttpError with a Gmail-shaped JSON body and httplib2 response."""
    gstatus, dreason, dmsg = _ERRORS.get(status, ("UNKNOWN", "unknown", "Error"))
    message = message or dmsg
    reason = reason or dreason
    headers = {"status": str(status), "content-type": "application/json; charset=UTF-8"}
    if retry_after is not None:
        headers["retry-after"] = str(retry_after)
    resp = httplib2.Response(headers)
    try:
        resp.reason = http.HTTPStatus(status).phrase
    except ValueError:
        resp.reason = "Error"
    domain = "usageLimits" if "RateLimit" in reason or "rateLimit" in reason else "global"
    body = {
        "error": {
            "code": status,
            "message": message,
            "errors": [{"message": message, "domain": domain, "reason": reason}],
            "status": gstatus,
        }
    }
    uri = f"https://gmail.googleapis.com/gmail/v1/users/me/{path}?alt=json"
    return HttpError(resp, json.dumps(body).encode(), uri=uri)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


@dataclass
class _Msg:
    id: str
    thread_id: str
    label_ids: list[str]
    internal_date: int
    history_id: int
    raw: bytes
    payload: dict
    snippet: str
    headers: dict[str, str] = field(default_factory=dict)  # lower-case, for q matching


@dataclass
class _Failure:
    remaining: int
    status: int
    method: str | None
    reason: str | None
    retry_after: float | None


class FakeRequest:
    """One prepared API call. Nothing happens until execute(), like HttpRequest."""

    def __init__(self, gmail: FakeGmail, method: str, kwargs: dict, fn):
        self.gmail, self.method, self.kwargs, self._fn = gmail, method, kwargs, fn

    def execute(self, num_retries: int = 0):
        return self.gmail._run(self)

    def __repr__(self) -> str:
        return f"<FakeRequest {self.method}>"


class FakeBatch:
    """BatchHttpRequest look-alike: callbacks get (request_id, response, exception)."""

    def __init__(self, gmail: FakeGmail, callback=None):
        self.gmail, self._callback = gmail, callback
        self._order: list[str] = []
        self._reqs: dict[str, tuple[FakeRequest, object]] = {}

    def add(self, request: FakeRequest, callback=None, request_id: str | None = None) -> None:
        if len(self._order) >= MAX_BATCH:
            raise BatchError(f"Exceeded maximum calls({MAX_BATCH}) in a single batch request.")
        request_id = request_id if request_id is not None else str(len(self._order) + 1)
        if request_id in self._reqs:
            raise KeyError(f"A request with this ID already exists: {request_id}")
        self._reqs[request_id] = (request, callback)
        self._order.append(request_id)

    def execute(self) -> None:
        g = self.gmail
        g.calls.append("batch")
        g._maybe_fail("batch", "batch")
        g.batches.append(len(self._order))
        results = []
        for rid in self._order:  # the server runs every part, then the client fires callbacks
            req, _ = self._reqs[rid]
            try:
                results.append((rid, req.execute(), None))
            except HttpError as e:
                results.append((rid, None, e))
        for rid, resp, exc in results:
            cb = self._reqs[rid][1]
            if cb is not None:
                cb(rid, resp, exc)
            if self._callback is not None:
                self._callback(rid, resp, exc)


class _Resource:
    def __init__(self, gmail: FakeGmail):
        self.g = gmail


class _Messages(_Resource):
    def list(self, **kw) -> FakeRequest:
        return self.g._req(
            "messages.list",
            kw,
            {"q", "labelIds", "maxResults", "pageToken", "includeSpamTrash", "fields"},
            self.g._list_messages,
        )

    def list_next(self, previous_request: FakeRequest, previous_response: dict) -> FakeRequest | None:
        return self.g._next(previous_request, previous_response, self.list)

    def get(self, **kw) -> FakeRequest:
        return self.g._req(
            "messages.get",
            kw,
            {"id", "format", "metadataHeaders", "fields"},
            self.g._get_message,
            required={"id"},
        )

    def modify(self, **kw) -> FakeRequest:
        return self.g._req(
            "messages.modify", kw, {"id", "body", "fields"}, self.g._modify_api, required={"id", "body"}
        )

    def batchModify(self, **kw) -> FakeRequest:
        return self.g._req("messages.batchModify", kw, {"body"}, self.g._batch_modify, required={"body"})


class _Labels(_Resource):
    def list(self, **kw) -> FakeRequest:
        return self.g._req("labels.list", kw, {"fields"}, self.g._list_labels)

    def get(self, **kw) -> FakeRequest:
        return self.g._req("labels.get", kw, {"id", "fields"}, self.g._get_label, required={"id"})

    def create(self, **kw) -> FakeRequest:
        return self.g._req("labels.create", kw, {"body", "fields"}, self.g._create_label, required={"body"})

    def patch(self, **kw) -> FakeRequest:
        return self.g._req(
            "labels.patch", kw, {"id", "body", "fields"}, self.g._patch_label, required={"id", "body"}
        )


class _History(_Resource):
    def list(self, **kw) -> FakeRequest:
        return self.g._req(
            "history.list",
            kw,
            {"startHistoryId", "historyTypes", "labelId", "pageToken", "maxResults", "fields"},
            self.g._list_history,
        )

    def list_next(self, previous_request: FakeRequest, previous_response: dict) -> FakeRequest | None:
        return self.g._next(previous_request, previous_response, self.list)


class _Users(_Resource):
    def messages(self) -> _Messages:
        return _Messages(self.g)

    def labels(self) -> _Labels:
        return _Labels(self.g)

    def history(self) -> _History:
        return _History(self.g)

    def getProfile(self, **kw) -> FakeRequest:
        return self.g._req("getProfile", kw, set(), self.g._profile)


class FakeGmail:
    """In-memory Gmail mailbox + service. See the module docstring for the API and helpers."""

    def __init__(
        self, email: str = "me@example.test", *, start_history_id: int = 1000, now_ms: int = 1_760_000_000_000
    ):
        self.email = email
        self.history_id = start_history_id
        self.now_ms = now_ms  # fake clock for internalDate and newer_than:
        self.calls: list[str] = []  # executed API methods, e.g. "messages.get", "batch"
        self.batches: list[int] = []  # size of each executed batch
        self._msgs: dict[str, _Msg] = {}
        self._labels: dict[str, dict] = {
            n: {
                "id": n,
                "name": n,
                "type": "system",
                "messageListVisibility": "hide",
                "labelListVisibility": "labelHide",
            }
            for n in SYSTEM_LABELS
        }
        self._history: list[dict] = []
        self._history_floor = start_history_id  # startHistoryId below this -> 404
        self._failures: list[_Failure] = []
        self._n = 0
        self._label_n = 0
        # backgroundColor/textColor values labels.create/patch reject with 400 (Gmail: outside its palette)
        self.invalid_colors: set[str] = set()

    # -- service surface -------------------------------------------------------------------

    def users(self) -> _Users:
        return _Users(self)

    def new_batch_http_request(self, callback=None) -> FakeBatch:
        return FakeBatch(self, callback)

    # -- helpers ---------------------------------------------------------------------------

    def deliver(
        self,
        msg: dict | str | bytes | EmailMessage | None = None,
        *,
        label_ids: list[str] | None = None,
        internal_date: int | None = None,
        thread_id: str | None = None,
        **fields,
    ) -> str:
        """Add an inbound message (records messagesAdded). Returns its id. See module docstring."""
        if internal_date is None:
            self.now_ms += 60_000
            internal_date = self.now_ms
        if isinstance(msg, (str, bytes)):
            raw = msg.encode() if isinstance(msg, str) else msg
            em = message_from_bytes(raw, policy=policy.default)
        elif isinstance(msg, EmailMessage):
            em = msg
        else:
            em = self._build({**(msg or {}), **fields}, internal_date)
        raw = em.as_bytes(policy=policy.default.clone(linesep="\r\n"))
        em = message_from_bytes(raw, policy=policy.default)
        labels = ["INBOX", "UNREAD"] if label_ids is None else [self._resolve(x) for x in label_ids]
        for lid in labels:
            if lid not in self._labels:
                raise ValueError(f"unknown label {lid}")
        self._n += 1
        mid = f"{0x19A0000000000000 + self._n:016x}"
        headers = {k.lower(): str(v) for k, v in em.items()}
        payload = self._payload(em, "")
        m = _Msg(
            mid,
            thread_id or mid,
            list(dict.fromkeys(labels)),
            internal_date,
            0,
            raw,
            payload,
            _snippet(em),
            headers,
        )
        self._msgs[mid] = m
        self._record(m, "messagesAdded")
        return mid

    def user_relabel(self, id: str, add=(), remove=()) -> None:
        """Simulate the user editing labels in the Gmail UI. Label ids or names are accepted."""
        self._apply(id, [self._resolve(x) for x in add], [self._resolve(x) for x in remove], check=False)

    def remove_message(self, id: str) -> None:
        """Permanently delete a message (records messagesDeleted); later gets 404."""
        m = self._msgs.pop(id)
        self._record(m, "messagesDeleted")

    def expire_history(self) -> None:
        """Drop retained history: any startHistoryId older than the current one now 404s."""
        self._history_floor = self.history_id
        self._history.clear()

    def fail_next(
        self,
        n: int = 1,
        status: int = 429,
        *,
        method: str | None = None,
        reason: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        """The next n matching executions raise HttpError(status). method=None matches any API
        method (each batch item counts singly); method="batch" fails a whole batch call."""
        self._failures.append(_Failure(n, status, method, reason, retry_after))

    def add_label(self, name: str, type: str = "user") -> str:
        """Create a label directly (no API call), e.g. to simulate another client's race."""
        return self._create_label({"name": name}, _type=type)["id"]

    def label_id(self, name: str) -> str | None:
        for lb in self._labels.values():
            if lb["name"].lower() == name.lower():
                return lb["id"]
        return None

    def labels_of(self, id: str) -> list[str]:
        return list(self._msgs[id].label_ids)

    def message_ids(self) -> list[str]:
        return list(self._msgs)

    # -- internals: plumbing ---------------------------------------------------------------

    def _req(self, method: str, kw: dict, allowed: set[str], fn, required: set[str] = frozenset()):
        kw = dict(kw)
        if "userId" not in kw:
            raise TypeError(f"{method}: missing required parameter 'userId'")
        user = kw.pop("userId")
        for k in kw:
            if k not in allowed:
                raise TypeError(f"{method}: got an unexpected keyword argument {k!r}")
        missing = required - set(kw)
        if missing:
            raise TypeError(f"{method}: missing required parameter {min(missing)!r}")
        if user not in ("me", self.email):
            raise http_error(403, "Delegation denied", reason="forbidden")
        return FakeRequest(self, method, {"userId": user, **kw}, lambda: fn(**kw))

    def _maybe_fail(self, method: str, path: str) -> None:
        for f in self._failures:
            if f.remaining > 0 and (f.method == method or (f.method is None and method != "batch")):
                f.remaining -= 1
                if f.remaining == 0:
                    self._failures.remove(f)
                raise http_error(f.status, reason=f.reason, retry_after=f.retry_after, path=path)

    def _run(self, req: FakeRequest):
        self.calls.append(req.method)
        self._maybe_fail(req.method, req.method.replace(".", "/"))
        return copy.deepcopy(req._fn())

    def _next(self, prev: FakeRequest, resp: dict, make):
        token = (resp or {}).get("nextPageToken")
        if not token:
            return None
        return make(**{**prev.kwargs, "pageToken": token})

    def _resolve(self, x: str) -> str:
        if x in self._labels:
            return x
        lid = self.label_id(x)
        if lid is None:
            raise ValueError(f"unknown label {x}")
        return lid

    def _record(self, m: _Msg, key: str, label_ids: list[str] | None = None, **extra) -> None:
        self.history_id += 1
        m.history_id = self.history_id
        stub = {"id": m.id, "threadId": m.thread_id, "labelIds": list(m.label_ids)}
        rec: dict = {"id": str(self.history_id), "messages": [{"id": m.id, "threadId": m.thread_id}]}
        if key in ("messagesAdded", "messagesDeleted"):
            rec[key] = [{"message": stub}]
        else:
            rec[key] = [{"message": stub, "labelIds": list(label_ids or [])}]
        for k, v in extra.items():
            rec[k] = [{"message": stub, "labelIds": list(v)}]
        self._history.append(rec)

    # -- internals: message building -------------------------------------------------------

    def _build(self, f: dict, internal_date: int) -> EmailMessage:
        em = EmailMessage()
        hdrs = {
            "From": f.get("from_", f.get("from", "Sender <sender@example.test>")),
            "To": f.get("to", self.email),
            "Cc": f.get("cc"),
            "Bcc": f.get("bcc"),
            "Subject": f.get("subject", "Test message"),
            "Date": f.get("date", formatdate(internal_date / 1000)),
            "Message-ID": f.get("message_id", make_msgid(domain="example.test")),
        }
        hdrs.update(f.get("headers") or {})
        for k, v in hdrs.items():
            if v is not None:
                em[k] = v
        cs = f.get("charset", "utf-8")
        body, html, cal = f.get("body"), f.get("html"), f.get("calendar")
        if body is None and html is None:
            body = ""
        if body is not None:
            em.set_content(body, charset=cs)
        if html is not None:
            if body is None:
                em.set_content(html, subtype="html", charset=cs)
            else:
                em.add_alternative(html, subtype="html", charset=cs)
        if cal:
            ics = cal if isinstance(cal, str) else _ICS
            em.add_alternative(ics, subtype="calendar", params={"method": "REQUEST"})
            em.add_attachment(ics.encode(), maintype="application", subtype="ics", filename="invite.ics")
        for name, mime, data in f.get("attachments") or []:
            main, _, sub = mime.partition("/")
            em.add_attachment(data, maintype=main, subtype=sub or "octet-stream", filename=name)
        return em

    def _payload(self, part, part_id: str) -> dict:
        out: dict = {
            "partId": part_id,
            "mimeType": part.get_content_type(),
            "filename": part.get_filename() or "",
            "headers": [{"name": k, "value": str(v)} for k, v in part.items()],
        }
        if part.is_multipart():
            out["body"] = {"size": 0}
            out["parts"] = [
                self._payload(p, f"{part_id}.{i}" if part_id else str(i))
                for i, p in enumerate(part.get_payload())
            ]
        else:
            data = part.get_payload(decode=True) or b""
            if out["filename"]:
                out["body"] = {
                    "attachmentId": f"ANGjdJ{self._n:06d}{part_id.replace('.', '')}",
                    "size": len(data),
                }
            else:
                out["body"] = {"size": len(data), "data": _b64(data)} if data else {"size": 0}
        return out

    # -- internals: API methods ------------------------------------------------------------

    def _profile(self) -> dict:
        return {
            "emailAddress": self.email,
            "messagesTotal": len(self._msgs),
            "threadsTotal": len({m.thread_id for m in self._msgs.values()}),
            "historyId": str(self.history_id),
        }

    def _list_messages(
        self, q=None, labelIds=None, maxResults=100, pageToken=None, includeSpamTrash=False, fields=None
    ) -> dict:
        want = [labelIds] if isinstance(labelIds, str) else list(labelIds or [])
        hidden = set() if includeSpamTrash or {"SPAM", "TRASH"} & set(want) else {"SPAM", "TRASH"}
        match = _query(q, self) if q else (lambda m: True)
        hits = [
            m
            for m in self._msgs.values()
            if all(w in m.label_ids for w in want) and not hidden & set(m.label_ids) and match(m)
        ]
        hits.sort(key=lambda m: (m.internal_date, m.id), reverse=True)
        start, size = int(pageToken or 0), max(1, min(500, int(maxResults)))
        page = hits[start : start + size]
        out: dict = {"resultSizeEstimate": len(hits)}
        if page:
            out["messages"] = [{"id": m.id, "threadId": m.thread_id} for m in page]
        if start + size < len(hits):
            out["nextPageToken"] = str(start + size)
        return out

    def _get_message(self, id, format="full", metadataHeaders=None, fields=None) -> dict:
        m = self._msgs.get(id)
        if m is None:
            raise http_error(404, path=f"messages/{id}")
        base = {
            "id": m.id,
            "threadId": m.thread_id,
            "labelIds": list(m.label_ids),
            "snippet": m.snippet,
            "sizeEstimate": len(m.raw),
            "historyId": str(m.history_id),
            "internalDate": str(m.internal_date),
        }
        if format == "minimal":
            return base
        if format == "full":
            return {**base, "payload": m.payload}
        if format == "metadata":
            want = {
                h.lower()
                for h in ([metadataHeaders] if isinstance(metadataHeaders, str) else metadataHeaders or [])
            }
            hs = [h for h in m.payload["headers"] if not want or h["name"].lower() in want]
            return {**base, "payload": {"partId": "", "mimeType": m.payload["mimeType"], "headers": hs}}
        if format == "raw":
            return {**base, "raw": base64.urlsafe_b64encode(m.raw).decode()}
        raise http_error(400, f"Invalid value at 'format' ({format})")

    def _check_labels(self, ids: list[str], which: str) -> None:
        for lid in ids:
            if lid not in self._labels:
                raise http_error(400, f"Invalid label: {lid}")
            if lid in IMMUTABLE_LABELS:
                raise http_error(400, f"Invalid label {lid} in {which}")

    def _apply(self, id: str, add: list[str], remove: list[str], check: bool = True) -> dict:
        m = self._msgs.get(id)
        if m is None:
            raise http_error(404, path=f"messages/{id}/modify")
        if check:
            self._check_labels(add, "AddLabelIds")
            self._check_labels(remove, "RemoveLabelIds")
        added = [x for x in dict.fromkeys(add) if x not in m.label_ids and x not in remove]
        removed = [x for x in dict.fromkeys(remove) if x in m.label_ids]
        m.label_ids = [x for x in m.label_ids if x not in removed] + added
        if added and removed:
            self._record(m, "labelsAdded", added, labelsRemoved=removed)
        elif added:
            self._record(m, "labelsAdded", added)
        elif removed:
            self._record(m, "labelsRemoved", removed)
        return {"id": m.id, "threadId": m.thread_id, "labelIds": list(m.label_ids)}

    def _modify_api(self, id, body, fields=None) -> dict:
        return self._apply(id, list(body.get("addLabelIds") or []), list(body.get("removeLabelIds") or []))

    def _batch_modify(self, body) -> str:
        ids = list(body.get("ids") or [])
        if not ids or len(ids) > 1000:
            raise http_error(400, "ids must contain 1..1000 message ids")
        add, remove = list(body.get("addLabelIds") or []), list(body.get("removeLabelIds") or [])
        self._check_labels(add, "AddLabelIds")
        self._check_labels(remove, "RemoveLabelIds")
        for mid in ids:
            if mid in self._msgs:  # unknown ids are ignored
                self._apply(mid, add, remove)
        return ""

    def _list_labels(self, fields=None) -> dict:
        return {"labels": list(self._labels.values())}

    def _get_label(self, id, fields=None) -> dict:
        if id not in self._labels:
            raise http_error(404, path=f"labels/{id}")
        return self._labels[id]

    def _create_label(self, body, fields=None, _type: str = "user") -> dict:
        name = (body.get("name") or "").strip()
        if not name or name.upper() in SYSTEM_LABELS:
            raise http_error(400, "Invalid label name")
        if self.label_id(name) is not None:  # Gmail collides case-insensitively
            raise http_error(409, path="labels")
        color = body.get("color")
        self._check_color(color)
        self._label_n += 1
        lb = {
            "id": f"Label_{self._label_n}",
            "name": name,
            "type": _type,
            "labelListVisibility": body.get("labelListVisibility", "labelShow"),
            "messageListVisibility": body.get("messageListVisibility", "show"),
        }
        if color:
            lb["color"] = dict(color)
        self._labels[lb["id"]] = lb
        return lb

    def _check_color(self, color) -> None:
        if color is None:
            return
        if set(color) != {"textColor", "backgroundColor"} or set(color.values()) & self.invalid_colors:
            raise http_error(400, "Label color is not on the allowed color palette", path="labels")

    def _patch_label(self, id, body, fields=None) -> dict:
        lb = self._labels.get(id)
        if lb is None:
            raise http_error(404, path=f"labels/{id}")
        if lb["type"] != "user":
            raise http_error(400, "Invalid update to system label", path=f"labels/{id}")
        self._check_color(body.get("color"))
        if "name" in body:
            name = (body.get("name") or "").strip()
            if not name or name.upper() in SYSTEM_LABELS:
                raise http_error(400, "Invalid label name", path=f"labels/{id}")
            if self.label_id(name) not in (None, id):  # Gmail collides case-insensitively
                raise http_error(409, path=f"labels/{id}")
            lb["name"] = name
        if "color" in body:
            lb["color"] = dict(body["color"])
        return lb

    def _list_history(
        self,
        startHistoryId=None,
        historyTypes=None,
        labelId=None,
        pageToken=None,
        maxResults=100,
        fields=None,
    ) -> dict:
        if startHistoryId is None:
            raise http_error(400, "Missing startHistoryId")
        start = int(startHistoryId)
        if start < self._history_floor:
            raise http_error(404, path="history")
        types = [historyTypes] if isinstance(historyTypes, str) else list(historyTypes or [])
        keys = [_HISTORY_KEYS[t] for t in types] if types else list(_HISTORY_KEYS.values())
        recs = []
        for rec in self._history:
            if int(rec["id"]) <= start:
                continue
            kept = {k: v for k, v in rec.items() if k in keys}
            if labelId:
                kept = {
                    k: [
                        i
                        for i in v
                        if labelId in i["message"]["labelIds"] or labelId in i.get("labelIds", [])
                    ]
                    for k, v in kept.items()
                }
                kept = {k: v for k, v in kept.items() if v}
            if kept:
                recs.append({"id": rec["id"], "messages": rec["messages"], **kept})
        offset, size = int(pageToken or 0), max(1, min(500, int(maxResults)))
        page = recs[offset : offset + size]
        out: dict = {"historyId": str(self.history_id)}
        if page:
            out["history"] = page
        if offset + size < len(recs):
            out["nextPageToken"] = str(offset + size)
        return out


_ICS = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\nUID:test@example.test\r\n"
    "DTSTART:20261101T170000Z\r\nDTEND:20261101T173000Z\r\nSUMMARY:Test meeting\r\n"
    "END:VEVENT\r\nEND:VCALENDAR\r\n"
)


def _snippet(em) -> str:
    part = em.get_body(preferencelist=("plain", "html")) if em.is_multipart() else em
    try:
        text = part.get_content() if part is not None else ""
    except (KeyError, LookupError, AttributeError):
        text = ""
    if part is not None and part.get_content_type() == "text/html":
        text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(str(text).split())[:200]


_UNITS = {"d": 86_400_000, "m": 30 * 86_400_000, "y": 365 * 86_400_000}


def _date_ms(v: str) -> int:
    if v.isdigit():
        return int(v) * 1000
    y, mo, d = (int(x) for x in v.split("/"))
    return int(time.mktime((y, mo, d, 0, 0, 0, 0, 0, -1)) * 1000)


def _norm(name: str) -> str:
    return name.lower().replace(" ", "-").replace("/", "-")


def _term(op: str, val: str, g: FakeGmail):
    """Predicate over _Msg for one search term."""
    if op in ("in", "label"):
        ids = {lid for lid, lb in g._labels.items() if _norm(lb["name"]) == _norm(val)}
        return lambda m: bool(ids & set(m.label_ids))
    if op == "category" or (op == "is" and val.lower() in ("unread", "starred", "important")):
        lid = f"CATEGORY_{val.upper()}" if op == "category" else val.upper()
        return lambda m: lid in m.label_ids
    if op in ("newer_than", "older_than"):
        cut = g.now_ms - int(val[:-1]) * _UNITS[val[-1].lower()]
        if op == "newer_than":
            return lambda m: m.internal_date > cut
        return lambda m: m.internal_date <= cut
    if op in ("after", "before"):
        c = _date_ms(val)
        if op == "after":
            return lambda m: m.internal_date >= c
        return lambda m: m.internal_date < c
    if op in ("from", "to", "subject"):
        v = val.lower()
        return lambda m: v in m.headers.get(op, "").lower()
    if op == "":
        v = val.lower()
        return lambda m: (
            v in " ".join((m.headers.get("subject", ""), m.headers.get("from", ""), m.snippet)).lower()
        )
    raise NotImplementedError(f"FakeGmail q operator {op!r} is not supported")


def _query(q: str, g: FakeGmail):
    """Compile a tiny subset of Gmail search syntax into a predicate over _Msg."""
    preds = []
    for tok in shlex.split(q):
        neg = tok.startswith("-")
        tok = tok[1:] if neg else tok
        op, sep, val = tok.partition(":")
        op, val = (op.lower(), val) if sep else ("", tok)
        preds.append((_term(op, val, g), neg))
    return lambda m: all(p(m) != neg for p, neg in preds)


__all__ = ["SYSTEM_LABELS", "FakeBatch", "FakeGmail", "FakeRequest", "http_error"]
