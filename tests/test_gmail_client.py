"""GmailClient against FakeGmail: retries, batching, history, pacing. No network."""

import json

import pytest
from googleapiclient.errors import HttpError

from email_town_labeler.gmail.client import GmailClient, HistoryExpired, api_disabled_project, is_retryable
from email_town_labeler.types import HistoryEvent
from tests.fakes.fake_service import FakeGmail, http_error


class Clock:
    """Fake monotonic clock; sleep() advances it and records each wait."""

    def __init__(self):
        self.t, self.waits = 0.0, []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.waits.append(s)
        self.t += s


def make(g=None, **kw):
    clock = Clock()
    kw.setdefault("max_gets_per_minute", None)
    return GmailClient(g or FakeGmail(), sleep=clock.sleep, clock=clock, **kw), clock


def fill(g, n):
    return [g.deliver(subject=f"m{i}", body=f"body {i}") for i in range(n)]


# -- retry classification ---------------------------------------------------------------


@pytest.mark.parametrize(
    "status,reason,want",
    [
        (429, None, True),
        (500, None, True),
        (503, None, True),
        (502, None, True),
        (403, "userRateLimitExceeded", True),
        (403, "rateLimitExceeded", True),
        (403, "insufficientPermissions", False),
        (404, None, False),
        (400, None, False),
        (409, None, False),
    ],
)
def test_is_retryable(status, reason, want):
    assert is_retryable(http_error(status, reason=reason)) is want


def test_is_retryable_ignores_non_http_errors():
    assert not is_retryable(ValueError("x"))


DISABLED = (
    "Gmail API has not been used in project 123456789012 before or it is disabled. Enable it by "
    "visiting https://console.developers.google.com/apis/api/gmail.googleapis.com/overview?project=123456789012"
)


@pytest.mark.parametrize(
    "err,want",
    [
        (http_error(403, DISABLED, reason="accessNotConfigured", path="profile"), "123456789012"),
        (http_error(403, "Gmail API is disabled.", reason="accessNotConfigured"), ""),
        (http_error(403, reason="insufficientPermissions"), None),
        (http_error(403, reason="userRateLimitExceeded"), None),
        (http_error(404), None),
        (ValueError("project 123"), None),
    ],
)
def test_api_disabled_project(err, want):
    assert api_disabled_project(err) == want


def test_api_disabled_project_reads_error_info_details():
    e = http_error(403, DISABLED, reason="forbidden")
    body = json.loads(e.content)
    body["error"]["details"] = [
        {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "SERVICE_DISABLED"}
    ]
    e.content = json.dumps(body).encode()
    assert api_disabled_project(e) == "123456789012"
    assert not is_retryable(e)


# -- single-call retries ------------------------------------------------------------------


def test_retries_then_succeeds_with_exponential_jittered_backoff():
    g = FakeGmail()
    c, clock = make(g)
    g.fail_next(3, 429)
    assert c.profile()["historyId"] == str(g.history_id)
    assert g.calls == ["getProfile"] * 4
    assert len(clock.waits) == 3
    for i, w in enumerate(clock.waits):
        assert 2**i <= w <= 2**i + 1  # base 2**attempt + jitter in [0, 1]


def test_backoff_is_capped():
    g = FakeGmail()
    c, clock = make(g, max_retries=9)
    g.fail_next(9, 503)
    c.profile()
    assert max(clock.waits) <= 65


def test_gives_up_after_max_retries():
    g = FakeGmail()
    c, clock = make(g, max_retries=2)
    g.fail_next(5, 500)
    with pytest.raises(HttpError) as ei:
        c.list_labels()
    assert ei.value.resp.status == 500 and len(clock.waits) == 2 and g.calls.count("labels.list") == 3


def test_rate_limit_403_retried_but_permission_403_is_not():
    g = FakeGmail()
    c, clock = make(g)
    g.fail_next(1, 403, reason="userRateLimitExceeded")
    c.list_labels()
    g.fail_next(1, 403, reason="insufficientPermissions")
    with pytest.raises(HttpError):
        c.list_labels()
    assert len(clock.waits) == 1


def test_retry_after_header_respected():
    g = FakeGmail()
    c, clock = make(g)
    g.fail_next(1, 429, retry_after=30)
    c.profile()
    assert clock.waits[0] >= 30


def test_404_not_retried():
    g = FakeGmail()
    c, clock = make(g)
    with pytest.raises(HttpError) as ei:
        c.get_label_ids("missing")
    assert ei.value.resp.status == 404 and clock.waits == []


# -- list / labels / modify ---------------------------------------------------------------


def test_list_message_ids_paginates_newest_first():
    g = FakeGmail()
    ids = fill(g, 7)
    c, _ = make(g)
    assert c.list_message_ids(max_results=100) == ids[::-1]
    assert c.list_message_ids(max_results=3) == ids[::-1][:3]
    big = fill(g, 600)
    got = c.list_message_ids(max_results=1000)
    assert len(got) == 607 and got[0] == big[-1] and g.calls.count("messages.list") >= 4


def test_list_message_ids_query_and_labels():
    g = FakeGmail()
    a = g.deliver(subject="a", label_ids=["INBOX", "CATEGORY_PROMOTIONS"])
    g.deliver(subject="b", label_ids=["SENT"])
    g.deliver(subject="c", label_ids=["INBOX", "SPAM"])
    c, _ = make(g)
    assert c.list_message_ids(label_ids=["INBOX"]) == [a]  # spam hidden by default
    assert c.list_message_ids(query="category:promotions") == [a]
    assert c.list_message_ids(query="in:inbox -category:promotions") == []
    assert len(c.list_message_ids(label_ids=["INBOX"], include_spam_trash=True)) == 2


def test_empty_list_has_no_messages_key():
    c, _ = make()
    assert c.list_message_ids() == []


def test_get_label_ids_and_modify():
    g = FakeGmail()
    mid = g.deliver(subject="x")
    lid = g.add_label("email-town-labeler/orders")
    c, _ = make(g)
    c.modify(mid, add=[lid], remove=["UNREAD"])
    assert sorted(c.get_label_ids(mid)) == sorted(["INBOX", lid])
    n = len(g.calls)
    c.modify(mid)  # no-op: no API call
    assert len(g.calls) == n


def test_modify_rejects_unknown_label():
    g = FakeGmail()
    mid = g.deliver(subject="x")
    c, _ = make(g)
    with pytest.raises(HttpError) as ei:
        c.modify(mid, add=["Label_999"])
    assert ei.value.resp.status == 400


def test_list_and_create_labels():
    c, _ = make()
    lb = c.create_label("email-town-labeler")
    assert lb["name"] == "email-town-labeler" and lb["type"] == "user"
    labels = c.list_labels()
    assert {"id", "name", "type"} == set(labels[0])
    assert any(x["id"] == lb["id"] for x in labels) and any(x["id"] == "INBOX" for x in labels)
    with pytest.raises(HttpError) as ei:
        c.create_label("EMAIL-TOWN-LABELER")  # case-insensitive collision
    assert ei.value.resp.status == 409


# -- batched get_records -------------------------------------------------------------------


def test_get_records_chunks_of_50_in_input_order():
    g = FakeGmail()
    ids = fill(g, 120)
    c, _ = make(g)
    order = ids[::-1]
    recs = c.get_records(order)
    assert [r["id"] for r in recs] == order
    assert g.batches == [50, 50, 20]
    assert recs[0]["body_text"].strip() == "body 119"


def test_get_records_skips_404s_and_dedupes():
    g = FakeGmail()
    ids = fill(g, 5)
    g.remove_message(ids[1])
    c, clock = make(g)
    recs = c.get_records(ids + ["deadbeef", ids[0]])
    assert [r["id"] for r in recs] == [ids[0], ids[2], ids[3], ids[4]]
    assert clock.waits == []


def test_get_records_retries_only_throttled_items():
    g = FakeGmail()
    ids = fill(g, 10)
    c, clock = make(g)
    g.fail_next(3, 429, method="messages.get")
    recs = c.get_records(ids)
    assert [r["id"] for r in recs] == ids
    assert g.batches == [10, 3] and len(clock.waits) == 1


def test_get_records_retries_whole_batch_failure():
    g = FakeGmail()
    ids = fill(g, 4)
    c, clock = make(g)
    g.fail_next(2, 503, method="batch")
    assert len(c.get_records(ids)) == 4 and len(clock.waits) == 2


def test_get_records_raises_on_persistent_throttle_and_fatal_errors():
    g = FakeGmail()
    ids = fill(g, 3)
    c, _ = make(g, max_retries=2)
    g.fail_next(100, 429, method="messages.get")
    with pytest.raises(HttpError):
        c.get_records(ids)
    g2 = FakeGmail()
    ids2 = fill(g2, 3)
    c2, _ = make(g2)
    g2.fail_next(1, 400, method="messages.get")
    with pytest.raises(HttpError) as ei:
        c2.get_records(ids2)
    assert ei.value.resp.status == 400


def test_batch_size_configurable_and_capped():
    g = FakeGmail()
    ids = fill(g, 25)
    c, _ = make(g, batch_size=10)
    c.get_records(ids)
    assert g.batches == [10, 10, 5]
    assert make(batch_size=500)[0].batch_size == 50


def test_token_bucket_paces_full_gets():
    g = FakeGmail()
    ids = fill(g, 200)
    c, clock = make(g, max_gets_per_minute=90)
    c.get_records(ids)
    # 200 gets at 90/min with a 50 burst: at least (200 - 50) / 1.5 per second = 100 s of waiting
    assert sum(clock.waits) >= 100 - 1e-6
    assert sum(clock.waits) <= 100 + 1e-6
    assert all(b <= 50 for b in g.batches)


def test_token_bucket_refills_with_time():
    g = FakeGmail()
    ids = fill(g, 40)
    c, clock = make(g, max_gets_per_minute=60)
    c.get_records(ids[:40])  # within the 50-token burst? capacity = min(50, 60) = 50
    assert clock.waits == []
    clock.t += 60
    c.get_records(ids[:40])
    assert clock.waits == []


def test_pacing_disabled():
    g = FakeGmail()
    ids = fill(g, 120)
    c, clock = make(g, max_gets_per_minute=None)
    c.get_records(ids)
    assert clock.waits == []


# -- history ---------------------------------------------------------------------------------


def test_history_maps_events_in_order():
    g = FakeGmail()
    start = str(g.history_id)
    lid = g.add_label("email-town-labeler/orders")
    a = g.deliver(subject="a")
    g.user_relabel(a, add=[lid], remove=["UNREAD"])
    g.user_relabel(a, remove=[lid])
    c, _ = make(g)
    events, new = c.history(start)
    assert new == str(g.history_id)
    assert events == [
        HistoryEvent("added", a, ["INBOX", "UNREAD"], ["INBOX", "UNREAD"]),
        HistoryEvent("label_added", a, [lid], ["INBOX", lid]),
        HistoryEvent("label_removed", a, ["UNREAD"], ["INBOX", lid]),
        HistoryEvent("label_removed", a, [lid], ["INBOX"]),
    ]


def test_history_requests_only_our_types():
    g = FakeGmail()
    start = str(g.history_id)
    a = g.deliver(subject="a")
    g.remove_message(a)  # messagesDeleted is not requested
    c, _ = make(g)
    events, _ = c.history(start)
    assert [e.kind for e in events] == ["added"]


def test_history_paginates():
    g = FakeGmail()
    start = str(g.history_id)
    ids = fill(g, 1203)
    c, _ = make(g)
    events, new = c.history(start)
    assert [e.message_id for e in events] == ids and new == str(g.history_id)
    assert g.calls.count("history.list") == 3


def test_history_empty_returns_current_id():
    g = FakeGmail()
    fill(g, 2)
    c, _ = make(g)
    assert c.history(str(g.history_id)) == ([], str(g.history_id))


def test_history_expired_raises():
    g = FakeGmail()
    start = str(g.history_id)
    fill(g, 3)
    g.expire_history()
    c, clock = make(g)
    with pytest.raises(HistoryExpired):
        c.history(start)
    assert clock.waits == []
    assert c.history(str(g.history_id)) == ([], str(g.history_id))  # current id still fine


def test_history_retries_transient_errors():
    g = FakeGmail()
    start = str(g.history_id)
    a = g.deliver(subject="a")
    c, clock = make(g)
    g.fail_next(2, 500, method="history.list")
    events, _ = c.history(start)
    assert [e.message_id for e in events] == [a] and len(clock.waits) == 2


# -- metadata ---------------------------------------------------------------------------------


def test_get_metadata_returns_only_the_requested_headers():
    g = FakeGmail()
    a = g.deliver(
        label_ids=["SENT"],
        subject="secret subject",
        body="secret body",
        to="Pal <pal@x.test>",
        cc="c@x.test",
        bcc="b@x.test",
        headers={"To": "second@x.test"},
    )
    b = g.deliver(subject="other")
    g.remove_message(b)
    c, _ = make(g)
    recs = c.get_metadata([a, b, a], ["To", "Cc", "Bcc"])
    assert [r["id"] for r in recs] == [a]  # 404s skipped, duplicates dropped
    r = recs[0]
    assert set(r["headers"]) == {"to", "cc", "bcc"} and "second@x.test" in r["headers"]["to"]
    assert r["label_ids"] == ["SENT"] and r["internal_date"] > 0
    assert "body_text" not in r and "snippet" not in r and "secret" not in repr(r)
    assert g.calls.count("messages.get") == 2 and g.calls.count("batch") == 1


def test_get_metadata_batches_and_paces_on_its_own_bucket():
    g = FakeGmail()
    ids = fill(g, 120)
    c, clock = make(g, max_gets_per_minute=90, max_metadata_per_minute=600)
    c.get_metadata(ids, ["To"])
    assert g.batches == [50, 50, 20]
    # 120 at 600/min (10/s) with a 50 burst: (120 - 50) / 10 = 7 s, far less than the full-get pace
    assert sum(clock.waits) == pytest.approx(7.0)
    g.fail_next(2, 429, method="messages.get")
    assert len(c.get_metadata(ids[:5], ["To"])) == 5  # throttled items are retried


def test_label_colors_round_trip():
    g = FakeGmail()
    c, _ = make(g)
    color = {"textColor": "#000000", "backgroundColor": "#ffad47"}
    lb = c.create_label("email-town-labeler/action needed", color=color)
    plain = c.create_label("email-town-labeler")
    by_id = {x["id"]: x for x in c.list_labels()}
    assert by_id[lb["id"]]["color"] == color and "color" not in by_id[plain["id"]]
    new = {"textColor": "#ffffff", "backgroundColor": "#16a766"}
    c.set_label_color(lb["id"], new)
    assert {x["id"]: x for x in c.list_labels()}[lb["id"]]["color"] == new
    g.invalid_colors.add("#123456")
    with pytest.raises(HttpError) as ei:
        c.set_label_color(lb["id"], {"textColor": "#000000", "backgroundColor": "#123456"})
    assert ei.value.resp.status == 400
