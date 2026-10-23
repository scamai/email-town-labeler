import math

from email_town_labeler.models.backends.rules import RulesClassifier, load
from email_town_labeler.models.base import Classifier
from email_town_labeler.taxonomy import CLASSES
from tests._model_fixtures import rec


def predict_one(r):
    (p,) = RulesClassifier().predict([r])
    assert set(p.probs) == set(CLASSES) and math.isclose(sum(p.probs.values()), 1.0)
    assert p.prob == max(p.probs.values()) and p.probs[p.cls] == p.prob
    return p


def test_protocol_and_empty():
    m = load()
    assert isinstance(m, Classifier) and m.manifest.backend == "rules" and m.predict([]) == []


def test_gmail_categories():
    assert (
        predict_one(rec(labels=["INBOX", "CATEGORY_PROMOTIONS"], list_unsubscribe="<x>")).cls == "promotional"
    )
    assert (
        predict_one(rec(labels=["INBOX", "CATEGORY_UPDATES"], sender="noreply@svc.example")).cls == "updates"
    )
    assert predict_one(rec(labels=["INBOX", "CATEGORY_SOCIAL"])).cls == "updates"


def test_headers_and_keywords():
    assert predict_one(rec(list_id="<news.example>", subject="The weekly digest")).cls == "newsletters"
    assert predict_one(rec(precedence="bulk", subject="Issue #12")).cls == "newsletters"
    shipped = rec(
        labels=["INBOX", "CATEGORY_UPDATES"], sender="no-reply@shop.example", subject="Your order shipped"
    )
    assert predict_one(shipped).cls == "orders"
    assert predict_one(rec(subject="Your flight itinerary")).cls == "travel"
    assert predict_one(rec(labels=["INBOX", "CATEGORY_PERSONAL"], subject="lunch?")).cls == "unknown"
    assert (
        predict_one(rec(labels=["INBOX", "CATEGORY_PERSONAL"], subject="Action required: please sign")).cls
        == "action_needed"
    )
    cal = rec(subject="Sync")
    cal["has_calendar"] = True
    assert predict_one(cal).cls == "updates"


def test_deterministic_and_order_preserving():
    rs = [rec(i, subject=s) for i, s in enumerate(["receipt", "hotel booking", "hi", "50% off sale"])]
    a, b = RulesClassifier().predict(rs), RulesClassifier().predict(list(rs))
    assert [(p.cls, p.probs) for p in a] == [(p.cls, p.probs) for p in b]
    assert [p.cls for p in a] == ["orders", "travel", "unknown", "promotional"]


def test_tolerates_missing_fields():
    p = predict_one({"id": "x"})
    assert p.cls == "unknown"


def test_ships_no_evaluation_claims():
    """The baseline must not carry numbers from any private evaluation."""
    m = load().manifest
    assert m.metrics == {} and "no published evaluation" in m.description
