"""The categories you choose in the desktop workbench teach a personal head (desktop/learning.py)."""

import os
from pathlib import Path

from email_town_labeler import learn
from email_town_labeler.config import Paths
from email_town_labeler.desktop.learning import DB_NAME
from email_town_labeler.desktop.manager import Workbench
from email_town_labeler.desktop.state import message_key
from email_town_labeler.models.base import ModelManifest
from email_town_labeler.types import Prediction

SUBJECT, SENDER = "Quarterly access request", "colleague@example.test"


class AskModel:
    """Requests look like weak updates to the model (action needed only 0.05); notices are sure updates."""

    manifest = ModelManifest("fixture", "1", "rules")

    def predict(self, records):
        out = []
        for record in records:
            if record.get("ask"):
                probs = {"updates": 0.60, "unknown": 0.35, "action_needed": 0.05}
            else:
                probs = {"updates": 0.90, "unknown": 0.08, "action_needed": 0.02}
            out.append(Prediction("updates", probs["updates"], probs))
        return out


class Reader:
    def __init__(self, ids):
        self.ids = ids

    def list(self, limit):
        rows = [
            {
                "provider_id": i,
                "headers": {"subject": SUBJECT, "from": SENDER},
                "internal_date": 1_700_000_000_000,
                "label_ids": [],
                "category": None,
            }
            for i in self.ids
        ]
        return rows[:limit], False

    def detail(self, provider_id):
        return {"body_text": "Please approve my access.", "ask": provider_id.startswith("ask")}


def _workbench(tmp_path: Path, ids, *, config: str = ""):
    paths = Paths.default(tmp_path / "config").ensure()
    paths.config_file.write_text('[model]\nref = "rules"\n' + config)
    reader = Reader(ids)
    workbench = Workbench(tmp_path / "wb", paths, reader_factory=lambda _a: reader, classifier=AskModel())
    known = workbench.accounts.all()
    aid = (
        known[0]["id"]
        if known
        else workbench.accounts.add("One", "imap", {"host": "one.example", "username": "u", "port": 993})[
            "id"
        ]
    )
    workbench._refresh()
    return workbench, aid


def _shown(workbench, aid, ids):
    rows = {row["id"]: row for row in workbench.snapshot()["messages"]}
    return {i: rows[message_key(aid, i)]["category"] for i in ids}


ASKS = [f"ask-{n}" for n in range(30)]
NOTICES = [f"notice-{n}" for n in range(30)]


def test_your_categories_teach_the_model_and_the_list_follows(tmp_path):
    workbench, aid = _workbench(tmp_path, ASKS + NOTICES)
    assert set(_shown(workbench, aid, ASKS + NOTICES).values()) == {"updates"}
    # You move 24 requests to Action needed and keep 10 notices in Updates; 6 requests are left alone.
    taught, untouched = ASKS[:24], ASKS[24:]
    workbench.action(
        {"ids": [message_key(aid, i) for i in taught], "action": "category", "category": "action_needed"}
    )
    workbench.action(
        {"ids": [message_key(aid, i) for i in NOTICES[:10]], "action": "category", "category": "updates"}
    )
    workbench._learn()
    assert workbench.learner.head is not None, workbench.learner.artifact.reason
    shown = _shown(workbench, aid, untouched + NOTICES[10:])
    # The requests you never touched now read as Action needed; the notices stay Updates.
    assert {shown[i] for i in untouched} == {"action_needed"}, shown
    assert {shown[i] for i in NOTICES[10:]} == {"updates"}, shown
    assert workbench.learner.head.id in workbench.classifier_stamp
    # New mail goes through the learned head as it arrives.
    workbench.readers[aid].ids.append("ask-new")
    workbench._refresh()
    assert _shown(workbench, aid, ["ask-new"]) == {"ask-new": "action_needed"}
    workbench.close()

    # Reopened: the learned head is used again, and the categories it made are kept, not redone.
    again, _ = _workbench(tmp_path, ASKS + NOTICES)
    assert again.learner.head is not None and again.classifier_stamp == workbench.classifier_stamp
    assert {_shown(again, aid, untouched)[i] for i in untouched} == {"action_needed"}
    again.close()


def test_too_few_categories_change_nothing(tmp_path):
    workbench, aid = _workbench(tmp_path, ASKS + NOTICES)
    stamp = workbench.classifier_stamp
    assert stamp == workbench.base_stamp  # nothing learned: an app update alone redoes no categories
    workbench.action(
        {"ids": [message_key(aid, i) for i in ASKS[:5]], "action": "category", "category": "action_needed"}
    )
    workbench._learn()
    assert workbench.learner.head is None and workbench.classifier_stamp == stamp
    assert set(_shown(workbench, aid, ASKS[5:]).values()) == {"updates"}
    workbench.close()


def test_learning_can_be_turned_off(tmp_path):
    workbench, aid = _workbench(tmp_path, ASKS + NOTICES, config="[learn]\nauto = false\n")
    workbench.action(
        {"ids": [message_key(aid, i) for i in ASKS[:24]], "action": "category", "category": "action_needed"}
    )
    workbench._learn()
    assert workbench.learner.head is None and not (tmp_path / "wb" / learn.FILENAME).exists()
    workbench.close()


def test_undo_takes_the_correction_back_and_only_numbers_are_stored(tmp_path):
    workbench, aid = _workbench(tmp_path, ASKS[:3])
    key = message_key(aid, ASKS[0])
    signals = workbench.learner.signals

    def corrections():
        rows = signals.learning_rows(workbench.learner.model_id)
        return {r["msg_id"]: r["fb_corrected"] for r in rows if r["fb"]}

    assert len(signals.learning_rows(workbench.learner.model_id)) == 3
    undo = workbench.action({"ids": [key], "action": "category", "category": "action_needed"})["undo"]
    assert corrections() == {key: "action_needed"}
    workbench.undo(undo)
    assert corrections() == {}
    workbench.close()
    stored = (tmp_path / "wb" / DB_NAME).read_bytes()
    for private in (SUBJECT, SENDER, "approve my access"):
        assert private.encode() not in stored
    if os.name != "nt":  # Windows has no POSIX modes: the file's privacy there is the user profile's ACL
        assert (tmp_path / "wb" / DB_NAME).stat().st_mode & 0o077 == 0
