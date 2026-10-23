"""FakeGmail fidelity: the shapes other tests rely on."""

import base64

import pytest
from googleapiclient.errors import BatchError, HttpError

from tests.fakes.fake_service import FakeGmail


@pytest.fixture
def g():
    return FakeGmail()


def msgs(g):
    return g.users().messages()


def test_get_formats(g):
    mid = g.deliver(subject="Hello", body="plain body", html="<p>html</p>")
    full = msgs(g).get(userId="me", id=mid, format="full").execute()
    assert full["payload"]["mimeType"] == "multipart/alternative"
    assert [p["mimeType"] for p in full["payload"]["parts"]] == ["text/plain", "text/html"]
    assert [p["partId"] for p in full["payload"]["parts"]] == ["0", "1"]
    assert full["internalDate"].isdigit() and full["historyId"] == str(g.history_id)
    assert full["snippet"] == "plain body" and full["sizeEstimate"] > 0
    data = full["payload"]["parts"][0]["body"]["data"]
    assert "=" not in data and "+" not in data
    minimal = msgs(g).get(userId="me", id=mid, format="minimal").execute()
    assert "payload" not in minimal and minimal["labelIds"] == ["INBOX", "UNREAD"]
    meta = msgs(g).get(userId="me", id=mid, format="metadata", metadataHeaders=["Subject"]).execute()
    assert meta["payload"]["headers"] == [{"name": "Subject", "value": "Hello"}]
    raw = msgs(g).get(userId="me", id=mid, format="raw").execute()["raw"]
    assert b"Subject: Hello" in base64.urlsafe_b64decode(raw)


def test_unknown_kwargs_and_missing_user_raise_typeerror(g):
    with pytest.raises(TypeError):
        msgs(g).get(userId="me", id="x", fromat="full")
    with pytest.raises(TypeError):
        msgs(g).list()


def test_missing_message_is_real_404(g):
    with pytest.raises(HttpError) as ei:
        msgs(g).get(userId="me", id="nope").execute()
    e = ei.value
    assert e.resp.status == 404 and e.status_code == 404 and "not found" in e.reason
    assert e.error_details[0]["reason"] == "notFound"


def test_list_paging_with_list_next(g):
    ids = [g.deliver(subject=str(i)) for i in range(5)]
    req, seen = msgs(g).list(userId="me", maxResults=2), []
    while req is not None:
        resp = req.execute()
        seen += [m["id"] for m in resp.get("messages", [])]
        req = msgs(g).list_next(req, resp)
    assert seen == ids[::-1]


def test_query_subset(g):
    old = g.deliver(subject="Old invoice", internal_date=g.now_ms - 40 * 86_400_000)
    new = g.deliver(subject="Weekly digest", from_="News <news@example.test>")

    def q(s):
        return [m["id"] for m in msgs(g).list(userId="me", q=s).execute().get("messages", [])]

    assert q("newer_than:7d") == [new] and q("older_than:30d") == [old]
    assert q("from:news@example.test") == [new] and q("invoice") == [old] and q("-invoice") == [new]
    assert q("is:unread in:inbox") == [new, old]
    with pytest.raises(NotImplementedError):
        q("has:attachment")


def test_modify_and_immutable_labels(g):
    mid = g.deliver(subject="x")
    out = (
        msgs(g)
        .modify(userId="me", id=mid, body={"addLabelIds": ["STARRED"], "removeLabelIds": ["UNREAD"]})
        .execute()
    )
    assert out["labelIds"] == ["INBOX", "STARRED"]
    with pytest.raises(HttpError) as ei:
        msgs(g).modify(userId="me", id=mid, body={"addLabelIds": ["SENT"]}).execute()
    assert ei.value.resp.status == 400


def test_batch_modify(g):
    ids = [g.deliver(subject=str(i)) for i in range(3)]
    lid = g.add_label("email-town-labeler/orders")
    assert msgs(g).batchModify(userId="me", body={"ids": ids, "addLabelIds": [lid]}).execute() == ""
    assert all(lid in g.labels_of(i) for i in ids)


def test_history_types_label_filter_and_noop(g):
    start = str(g.history_id)
    a = g.deliver(subject="a")
    g.user_relabel(a, add=["STARRED"])
    g.user_relabel(a, add=["STARRED"])  # no-op: no history record
    h = g.users().history()
    full = h.list(userId="me", startHistoryId=start).execute()
    assert len(full["history"]) == 2 and full["historyId"] == str(g.history_id)
    only = h.list(userId="me", startHistoryId=start, historyTypes="labelAdded").execute()
    assert list(only["history"][0]) == ["id", "messages", "labelsAdded"]
    assert h.list(userId="me", startHistoryId=start, labelId="STARRED").execute()["history"][0]["labelsAdded"]
    assert "history" not in h.list(userId="me", startHistoryId=str(g.history_id)).execute()


def test_history_ids_monotonic_and_expiry(g):
    seen = []
    for i in range(3):
        g.deliver(subject=str(i))
        seen.append(g.history_id)
    assert seen == sorted(set(seen))
    g.expire_history()
    with pytest.raises(HttpError) as ei:
        g.users().history().list(userId="me", startHistoryId=str(seen[0])).execute()
    assert ei.value.resp.status == 404


def test_batch_callbacks_ids_and_limits(g):
    ids = [g.deliver(subject=str(i)) for i in range(3)]
    got, per = [], []
    b = g.new_batch_http_request(callback=lambda rid, resp, exc: got.append((rid, resp and resp["id"], exc)))
    b.add(msgs(g).get(userId="me", id=ids[0]), callback=lambda *a: per.append(a[0]))
    b.add(msgs(g).get(userId="me", id="missing"))
    b.add(msgs(g).get(userId="me", id=ids[2]), request_id="mine")
    with pytest.raises(KeyError):
        b.add(msgs(g).get(userId="me", id=ids[1]), request_id="mine")
    b.execute()
    assert per == ["1"] and [r[0] for r in got] == ["1", "2", "mine"]
    assert got[0][1] == ids[0] and got[1][2].resp.status == 404 and got[2][1] == ids[2]
    big = g.new_batch_http_request()
    for i in range(100):
        big.add(msgs(g).get(userId="me", id=ids[0]))
    with pytest.raises(BatchError):
        big.add(msgs(g).get(userId="me", id=ids[0]))


def test_fail_next_scoping(g):
    g.fail_next(1, 500, method="labels.list")
    g.users().getProfile(userId="me").execute()  # not matched
    with pytest.raises(HttpError):
        g.users().labels().list(userId="me").execute()
    g.users().labels().list(userId="me").execute()  # consumed


def test_label_create_collisions(g):
    lb = g.users().labels()
    lb.create(userId="me", body={"name": "Work"}).execute()
    for name, status in (("work", 409), ("inbox", 400), ("", 400)):
        with pytest.raises(HttpError) as ei:
            lb.create(userId="me", body={"name": name}).execute()
        assert ei.value.resp.status == status


def test_profile(g):
    g.deliver(subject="a")
    p = g.users().getProfile(userId="me").execute()
    assert p == {
        "emailAddress": "me@example.test",
        "messagesTotal": 1,
        "threadsTotal": 1,
        "historyId": str(g.history_id),
    }
