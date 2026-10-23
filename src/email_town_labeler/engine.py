"""The sync loop: Gmail history -> eligible new mail -> classify -> policy -> add one label; capture corrections.

Between model.predict and labeling, policy.decide turns probabilities into the final class (user-priority
thresholds, the known-contact guard, the confidence bar, the new-sender rule). Contacts (addresses you have
sent mail to) come from a one-time scan of sent mail and then from SENT messages seen in history.

Each decision also stores the model's own scores and a few yes/no features (learn.py), and labels another
labeler (Town, the "teacher") puts on mail we decided. With `learn_path`, a personal head and learned
thresholds from learn.py are applied, reloaded when the file changes, and (in `run_forever`) re-learned on
schedule.

Logs carry message ids, classes, probabilities and counts only — never mail content or addresses.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from . import learn
from .config import Config
from .contacts import recipient_addresses, sender_address
from .policy import Decision, decide
from .state import Store
from .taxonomy import UNKNOWN
from .types import EmailRecord, HistoryEvent, Prediction

log = logging.getLogger("email_town_labeler.engine")

EXCLUDED = frozenset({"SENT", "DRAFT", "SPAM", "TRASH"})
DAY = 86_400.0
RECOVERY_SLACK = 3600.0  # rescan a little before the last good poll: internalDate can trail the history event
RECOVERY_LEGACY_DAYS = 2  # state from before bootstrap/poll times were stored: the old fixed window
RECOVERY_MAX_IDS = 5000
BATCH = 32
MAX_BACKOFF = 900.0
LABEL_CACHE_SECONDS = 3600.0
CONTACT_HEADERS = ["To", "Cc", "Bcc"]
CONTACTS_TS = "contacts_ts"  # kv: when the sent-mail contacts scan last completed
PRIMARY = "CATEGORY_PERSONAL"
TEACHER_AUTO_MIN = 3  # learn.teacher = "auto": at least this many teacher label names exist in the account
LEARN_RECHECK = 3600.0  # seconds between checks whether a scheduled learning run is due


def is_history_expired(exc: BaseException) -> bool:
    """Match gmail.client.HistoryExpired (or any subclass) without importing the Gmail layer."""
    return any(c.__name__ == "HistoryExpired" for c in type(exc).__mro__)


def _err_name(exc: BaseException) -> str:
    status = getattr(getattr(exc, "resp", None), "status", None) or getattr(exc, "status_code", None)
    return f"{type(exc).__name__}{f' {status}' if status else ''}"


class Engine:
    def __init__(
        self,
        client: Any,
        labels: Any,
        model: Any,
        store: Store,
        cfg: Config,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] | None = None,
        batch_size: int = BATCH,
        learn_path: Path | None = None,
    ):
        self.client, self.labels, self.model, self.store = client, labels, model, store
        self.clock, self._sleep_fn, self.batch_size = clock, sleep, batch_size
        self.config = cfg  # as loaded; self.cfg adds the model's / learned thresholds
        self.learn_path = Path(learn_path) if learn_path is not None else None
        self.artifact: learn.Artifact | None = None
        self._artifact_stamp: tuple[int, int] | None = None
        self._next_learn_check = float("-inf")
        self._label_index: dict[str, str] = {}  # label id -> name
        self._label_index_ts = float("-inf")
        self._set_cfg()
        self.reload_learned(force=True)

    # -- learned head + thresholds ------------------------------------------------------------
    def _set_cfg(self) -> None:
        # thresholds: config.toml, else learned (learn.py), else the model's recommended, else defaults
        manifest = getattr(self.model, "manifest", None) if self.model is not None else None
        learned = self.artifact.policy if self.artifact else None
        self.cfg = self.config.for_model(manifest, learned) if self.model is not None else self.config

    @property
    def head(self) -> learn.PersonalHead | None:
        return self.artifact.active_head if self.artifact else None

    def reload_learned(self, force: bool = False) -> bool:
        """(Re)load learn_path if it changed (another process may have run `learn`). True if it changed."""
        if self.learn_path is None or self.model is None:
            return False
        try:
            st = self.learn_path.stat()
            stamp: tuple[int, int] | None = (st.st_mtime_ns, st.st_size)
        except OSError:
            stamp = None
        if not force and stamp == self._artifact_stamp:
            return False
        self._artifact_stamp = stamp
        before = self.head.id if self.head else None
        self.artifact = learn.load(self.learn_path, self.model_id) if stamp else None
        self._set_cfg()
        after = self.head.id if self.head else None
        if after != before or not force:
            log.info(
                "learned settings: head %s; action_threshold %g, promotional_threshold %g",
                after or "none",
                self.cfg.action_threshold,
                self.cfg.promotional_threshold,
            )
        return True

    def learn(self, *, dry_run: bool = False) -> learn.Result:
        """One learning run now (needs learn_path). No Gmail calls, no mail content."""
        if self.learn_path is None or self.model is None:
            raise RuntimeError("learning needs a model and learn_path")
        res = learn.learn_once(
            self.store,
            self.learn_path,
            self.model_id,
            self.config,
            self.model.manifest,
            now=self.clock(),
            save_it=not dry_run,
        )
        if not dry_run:
            self.reload_learned(force=True)
        return res

    def maybe_learn(self) -> learn.Result | None:
        """The scheduled run (`[learn] auto`): every interval_hours, when there are enough new signals."""
        if self.learn_path is None or self.model is None or not self.config.learn_auto:
            return None
        now = self.clock()
        if now < self._next_learn_check:
            return None
        self._next_learn_check = now + LEARN_RECHECK
        ok, _ = learn.due(self.store, self.model_id, self.config, now=now)
        return self.learn() if ok else None

    # -- teacher labels -----------------------------------------------------------------------
    def _teacher_ids(self) -> dict[str, str | None]:
        """Gmail label id -> the model class a teacher (Town) label means (None: no class), if enabled."""
        mode = self.config.learn_teacher
        if mode == "off":
            return {}
        by_name: dict[str, str] = {}
        for key, name in self.config.teacher_names().items():
            by_name.setdefault(name.strip().casefold(), key)
        p = self.cfg.label_prefix.casefold()
        out: dict[str, str | None] = {}
        for lid, name in self._label_index.items():
            n = name.strip().casefold()
            if n in by_name and n != p and not n.startswith(p + "/"):
                out[lid] = learn.model_class(by_name[n])
        if mode == "auto" and len(out) < TEACHER_AUTO_MIN:
            return {}
        return out

    def capture_teacher(self, events: list[HistoryEvent]) -> int:
        """Teacher labels added to / removed from messages we decided -> teacher rows. Returns changes."""
        tids = self._teacher_ids()
        if not tids:
            return 0
        touched: dict[str, list[HistoryEvent]] = {}
        for e in events:
            if e.kind in ("label_added", "label_removed") and set(e.label_ids) & set(tids):
                touched.setdefault(e.message_id, []).append(e)
        n = 0
        for mid, evs in touched.items():
            if self.store.decision(mid) is None:
                continue  # not decided (yet): classification reads the label off the message instead
            final = {lid for lid in evs[-1].message_label_ids if lid in tids}
            added = [lid for e in evs if e.kind == "label_added" for lid in e.label_ids if lid in tids]
            if final:
                self.store.record_teacher(mid, tids[_pick(final, added)])
            else:
                self.store.delete_teacher(mid)
            n += 1
        return n

    @property
    def model_id(self) -> str:
        return self.model.manifest.id

    # -- labels -------------------------------------------------------------------------------
    def _refresh_labels(self, force: bool = False) -> None:
        now = self.clock()
        if force or now - self._label_index_ts > LABEL_CACHE_SECONDS:
            self._label_index = {lb["id"]: lb.get("name", "") for lb in self.client.list_labels()}
            self._label_index_ts = now
            if force and hasattr(self.labels, "refresh"):
                self.labels.refresh()  # the real LabelMap caches label names too

    def sync_label_colors(self) -> int:
        """Bring our existing class labels' colors in line with config (not in dry-run). Never raises:
        a color problem must not stop the daemon."""
        if self.cfg.dry_run or not hasattr(self.labels, "sync_colors"):
            return 0
        try:
            return self.labels.sync_colors()
        except Exception as e:  # noqa: BLE001 - a color problem must not stop the daemon
            log.warning("could not update label colors (%s); continuing", _err_name(e))
            return 0

    def _ours(self) -> set[str]:
        p = self.cfg.label_prefix
        under = {i for i, n in self._label_index.items() if n == p or n.startswith(p + "/")}
        return set(self.labels.our_label_ids()) | under

    def _skip_ids(self) -> set[str]:
        wanted = {n.casefold() for n in self.cfg.skip_label_names}
        return {i for i, n in self._label_index.items() if n.casefold() in wanted or i.casefold() in wanted}

    def _see_labels(self, label_ids: Iterable[str]) -> None:
        missing = {lid for lid in label_ids if lid not in self._label_index}
        if missing:
            self._refresh_labels(force=True)
            for lid in missing:  # never listed (e.g. hidden system ids): don't refresh for them again
                self._label_index.setdefault(lid, "")

    # -- eligibility + classification ---------------------------------------------------------
    def ineligible_reason(self, rec: EmailRecord) -> str | None:
        labs = set(rec.get("label_ids") or [])
        if "INBOX" not in labs:
            return "not_inbox"
        if labs & EXCLUDED:
            return "excluded"
        if labs & self._ours():
            return "ours"
        if labs & self._skip_ids():
            return "skip_label"
        return None

    def contact_flags(self, recs: list[EmailRecord]) -> list[tuple[bool, bool]]:
        """(known contact, new address) per message; both False when unknowable (before the scan)."""
        senders = [sender_address(r) for r in recs]
        ready = self.cfg.uses_contacts() and self.contacts_ready()
        known = self.store.known_contacts([s for s in senders if s]) if ready else set()
        me = (self.store.get("account_email") or "").lower()
        out = []
        for s in senders:
            is_known = bool(s) and (s in known or s == me)
            out.append((is_known, ready and bool(s) and not is_known))
        return out

    def decisions(
        self,
        recs: list[EmailRecord],
        preds: list[Prediction],
        flags: list[tuple[bool, bool]] | None = None,
    ) -> list[Decision]:
        """policy.decide for each message: needs its sender's contact status and its Gmail category."""
        flags = self.contact_flags(recs) if flags is None else flags
        return [
            decide(
                pred,
                known_contact=known,
                new_address=new,
                primary=PRIMARY in (rec.get("label_ids") or []),
                cfg=self.cfg,
            )
            for rec, pred, (known, new) in zip(recs, preds, flags)
        ]

    def predict(self, recs: list[EmailRecord]) -> tuple[list[Prediction], list[Any], list[list[float]], list]:
        """The model (then the personal head, if active): predictions, base scores, features, contact flags."""
        preds = self.model.predict(recs)
        if len(preds) != len(recs):
            raise RuntimeError(f"model returned {len(preds)} predictions for {len(recs)} messages")
        flags = self.contact_flags(recs)
        scores = [learn.base_scores(p) for p in preds]
        feats = [learn.features(r, known_contact=k) for r, (k, _) in zip(recs, flags)]
        head = self.head
        return (head.predictions(scores, feats) if head and recs else preds), scores, feats, flags

    def _apply(
        self,
        rec: EmailRecord,
        pred: Prediction,
        d: Decision,
        counts: dict[str, Any],
        *,
        scores: Any = None,
        feats: list[float] | None = None,
    ) -> None:
        cls = d.cls
        mid = rec["id"]
        apply = not (cls == UNKNOWN and self.cfg.unknown_action == "skip") and not self.cfg.dry_run
        if apply:
            lid = self.labels.ensure(cls)
            self.client.modify(mid, add=[lid])
            self._label_index.setdefault(lid, "")
            counts["labeled"] += 1
        else:
            counts["recorded"] += 1
        self.store.record_decision(
            mid,
            cls,
            pred.prob,
            pred.probs,
            self.model_id,
            apply,
            dry_run=self.cfg.dry_run,
            model_cls=pred.cls,
            scores=None if scores is None else list(scores),
            feats=feats,
            head_id=self.head.id if self.head else None,
        )
        tids = self._teacher_ids()
        on = [lid for lid in rec.get("label_ids") or [] if lid in tids]
        if on:  # the teacher labeled it before we did
            self.store.record_teacher(mid, tids[_pick(set(on), on)])
        counts["by_class"][cls] = counts["by_class"].get(cls, 0) + 1
        log.debug("decided %s -> %s (model %s p=%.3f) applied=%s", mid, cls, pred.cls, pred.prob, apply)

    def classify_ids(self, ids: list[str], counts: dict[str, Any] | None = None) -> dict[str, Any]:
        """Fetch (deduped, skipping already decided ids), filter eligibility, predict in batches, apply."""
        counts = counts if counts is not None else _counts()
        ids = list(dict.fromkeys(ids))
        done = self.store.decided(ids, include_dry_run=self.cfg.dry_run)
        todo = [i for i in ids if i not in done]
        counts["already_decided"] += len(ids) - len(todo)
        if not todo:
            return counts
        recs = self.client.get_records(todo)
        counts["fetched"] += len(recs)
        self._refresh_labels()
        self._see_labels(lid for r in recs for lid in r.get("label_ids") or [])
        ok: list[EmailRecord] = []
        for r in recs:
            why = self.ineligible_reason(r)
            if why:
                counts["skipped"] += 1
                counts["skip_reasons"][why] = counts["skip_reasons"].get(why, 0) + 1
            else:
                ok.append(r)
        for i in range(0, len(ok), self.batch_size):
            batch = ok[i : i + self.batch_size]
            preds, scores, feats, flags = self.predict(batch)
            for rec, pred, d, z, f in zip(batch, preds, self.decisions(batch, preds, flags), scores, feats):
                self._apply(rec, pred, d, counts, scores=z, feats=f)
        return counts

    # -- contacts -----------------------------------------------------------------------------
    def contacts_ready(self) -> bool:
        return self.store.get(CONTACTS_TS) is not None

    def _sent_recipients(self, ids: list[str]) -> dict[str, int]:
        """address -> earliest send time (ms) over the SENT messages among ids (headers only)."""
        found: dict[str, int] = {}
        if not ids:
            return found
        for r in self.client.get_metadata(list(dict.fromkeys(ids)), CONTACT_HEADERS):
            if "SENT" not in (r.get("label_ids") or []):
                continue
            ms = int(r.get("internal_date") or 0)
            for a in recipient_addresses(r):
                found[a] = min(found.get(a, ms), ms)
        return found

    def rebuild_contacts(self) -> dict[str, int]:
        """Scan sent mail (last contacts_scan_days, at most contacts_scan_limit) and replace the contacts."""
        cfg = self.cfg
        ids = (
            self.client.list_message_ids(
                query=f"newer_than:{cfg.contacts_scan_days}d",
                label_ids=["SENT"],
                max_results=cfg.contacts_scan_limit,
            )
            if cfg.contacts_scan_limit
            else []
        )
        n = self.store.replace_contacts(self._sent_recipients(ids))
        self.store.set(CONTACTS_TS, repr(self.clock()))
        log.info("contacts rebuilt: %d addresses from %d sent messages", n, len(ids))
        return {"contacts": n, "sent_scanned": len(ids)}

    def ensure_contacts(self) -> None:
        """The one-time sent-mail scan, when the policy uses contacts and it has not run yet."""
        if self.cfg.uses_contacts() and not self.contacts_ready():
            self.rebuild_contacts()

    def learn_contacts(self, sent_ids: list[str]) -> int:
        """Add the recipients of newly sent mail. Returns how many addresses are new."""
        if not sent_ids or not self.cfg.uses_contacts() or not self.contacts_ready():
            return 0
        n = self.store.add_contacts(self._sent_recipients(sent_ids))
        if n:
            log.info("contacts: %d new from %d sent messages", n, len(set(sent_ids)))
        return n

    # -- feedback -----------------------------------------------------------------------------
    def capture_feedback(self, events: list[HistoryEvent]) -> int:
        """User removed/added one of our class labels -> (predicted, corrected) rows."""
        self._refresh_labels()
        self._see_labels(lid for e in events if e.kind != "added" for lid in e.label_ids)
        ours = self._ours()
        touched: dict[str, list[HistoryEvent]] = {}
        for e in events:
            if e.kind in ("label_added", "label_removed") and set(e.label_ids) & ours:
                touched.setdefault(e.message_id, []).append(e)
        n = 0
        for mid, evs in touched.items():
            final = {c for c in map(self.labels.class_of, evs[-1].message_label_ids) if c}
            added = [
                c for e in evs if e.kind == "label_added" for c in map(self.labels.class_of, e.label_ids) if c
            ]
            d = self.store.decision(mid)
            if d is None:  # a message we never decided: only a user-added label of ours is a signal
                if final:
                    self.store.record_feedback(mid, None, _pick(final, added))
                    n += 1
                continue
            expected = set(filter(None, (d.get("current") or "").split(",")))
            if final == expected:
                continue  # e.g. the label we added ourselves
            new = final - expected
            corrected = _pick(new, added) if new else None
            self.store.record_feedback(mid, d["cls"], corrected)
            self.store.set_current(mid, ",".join(sorted(final)) or None)
            n += 1
            log.info("feedback %s: %s -> %s", mid, d["cls"], corrected)
        return n

    # -- polling ------------------------------------------------------------------------------
    def bootstrap(self) -> dict[str, Any]:
        prof = self.client.profile()
        self.store.set("bootstrap_ts", repr(self.clock()))  # mail before this is backfill-only, forever
        self.store.set("history_id", str(prof["historyId"]))
        if prof.get("emailAddress"):
            self.store.set("account_email", prof["emailAddress"])
        log.info("bootstrapped at history %s; existing mail is left alone (use backfill)", prof["historyId"])
        self.ensure_contacts()  # after the pointer is stored: mail sent during the scan comes via history
        return {**_counts(), "bootstrapped": True}

    def recovery_since(self) -> float:
        """Epoch seconds to rescan the inbox from after the history pointer expired.

        The last successful poll (minus a little slack), but never more than `recovery_max_days` back and
        never before the bootstrap time: mail that predates setup stays backfill-only.
        """
        now = self.clock()
        last, boot = _num(self.store.get("last_ok_poll_start")), _num(self.store.get("bootstrap_ts"))
        if last is not None:
            since = last - RECOVERY_SLACK
        elif boot is not None:
            since = boot
        else:
            since = now - RECOVERY_LEGACY_DAYS * DAY
        since = max(since, now - self.cfg.recovery_max_days * DAY)
        return max(since, boot) if boot is not None else since

    def recover(self) -> dict[str, Any]:
        """History pointer expired: classify inbox mail since the last good poll, then restart history from now."""
        prof = self.client.profile()
        since = self.recovery_since()
        log.warning(
            "history expired; scanning inbox mail from the last %.1f days and re-bootstrapping",
            max(0.0, self.clock() - since) / DAY,
        )
        ids = self.client.list_message_ids(
            query=f"after:{int(since)}", label_ids=["INBOX"], max_results=RECOVERY_MAX_IDS
        )
        if len(ids) >= RECOVERY_MAX_IDS:
            log.warning(
                "recovery scan hit %d messages; older ones are left for `email-town-labeler backfill`",
                len(ids),
            )
        added = 0
        if self.cfg.uses_contacts() and self.contacts_ready():  # mail you sent during the gap
            added = self.learn_contacts(
                self.client.list_message_ids(
                    query=f"after:{int(since)}", label_ids=["SENT"], max_results=RECOVERY_MAX_IDS
                )
            )
        counts = self.classify_ids(ids)
        counts["contacts_added"] = added
        learn.add_history_gap(self.store, since, self.clock())  # teacher label events here are lost
        self.store.set("history_id", str(prof["historyId"]))
        counts["recovered"] = True
        counts["new"] = len(ids)
        return counts

    def sync_once(self) -> dict[str, Any]:
        t0 = self.clock()
        self.store.set("model_id", self.model_id)
        self.reload_learned()
        start = self.store.get("history_id")
        if start is None:
            counts = self.bootstrap()
        else:
            self.ensure_contacts()  # state from before contacts existed: scan once
            try:
                events, new_hid = self.client.history(start)
            except Exception as e:
                if not is_history_expired(e):
                    raise
                counts = self.recover()
            else:
                counts = _counts()
                counts["events"] = len(events)
                counts["contacts_added"] = self.learn_contacts(_sent_ids(events))  # before classifying
                counts["feedback"] = self.capture_feedback(events)
                counts["teacher"] = self.capture_teacher(events)
                added = list(dict.fromkeys(e.message_id for e in events if e.kind == "added"))
                counts["new"] = len(added)
                self.classify_ids(added, counts)
                self.store.set("history_id", str(new_hid))
        now = self.clock()
        self.store.set(
            "last_ok_poll_start", repr(t0)
        )  # what was seen up to here is safe: recovery starts here
        self.store.set("last_poll", repr(now))
        self.store.set("last_poll_counts", json.dumps(counts, sort_keys=True))
        log.info(
            "poll ok%s: events=%d new=%d fetched=%d labeled=%d recorded=%d skipped=%d dup=%d "
            "feedback=%d ms=%d%s",
            " [dry-run]" if self.cfg.dry_run else "",
            counts["events"],
            counts["new"],
            counts["fetched"],
            counts["labeled"],
            counts["recorded"],
            counts["skipped"],
            counts["already_decided"],
            counts["feedback"],
            int((now - t0) * 1000),
            " bootstrapped"
            if counts.get("bootstrapped")
            else " recovered"
            if counts.get("recovered")
            else "",
        )
        return counts

    def _wait(self, seconds: float, stop: threading.Event) -> None:
        if self._sleep_fn is not None:
            self._sleep_fn(seconds)
        else:
            stop.wait(seconds)

    def run_forever(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        failures = 0
        log.info(
            "running: model=%s poll=%ss dry_run=%s", self.model_id, self.cfg.poll_seconds, self.cfg.dry_run
        )
        self.sync_label_colors()
        while not stop.is_set():
            try:
                self.sync_once()
                failures = 0
                delay = float(self.cfg.poll_seconds)
                try:
                    self.maybe_learn()
                except Exception as e:  # learning must never stop labeling
                    log.warning("learning failed (%s); labeling continues", _err_name(e))
                    log.debug("learning failure detail", exc_info=True)
            except Exception as e:  # transient API/network/model errors must not kill the daemon
                failures += 1
                delay = min(15.0 * 2 ** (failures - 1), MAX_BACKOFF)
                self.store.set("last_error", f"{_err_name(e)} at {self.clock():.0f}")
                log.warning("poll failed (%s, %d in a row); retrying in %.0fs", _err_name(e), failures, delay)
                log.debug("poll failure detail", exc_info=True)
            if stop.is_set():
                break
            self._wait(delay, stop)
        log.info("stopped")

    # -- one-shot operations ------------------------------------------------------------------
    def backfill(self, *, days: int | None = None, limit: int | None = None) -> dict[str, Any]:
        query = f"newer_than:{int(days)}d" if days else None
        self.ensure_contacts()
        ids = self.client.list_message_ids(query=query, label_ids=["INBOX"], max_results=limit or 500)
        counts = self.classify_ids(ids)
        counts["scanned"] = len(ids)
        log.info(
            "backfill: scanned=%d labeled=%d recorded=%d skipped=%d dup=%d",
            len(ids),
            counts["labeled"],
            counts["recorded"],
            counts["skipped"],
            counts["already_decided"],
        )
        return counts

    def preview(self, n: int = 5) -> list[dict[str, Any]]:
        """Classify the n newest inbox messages without recording or modifying anything."""
        ids = self.client.list_message_ids(label_ids=["INBOX"], max_results=n)
        recs = self.client.get_records(ids[:n])
        if not recs:
            return []
        preds, _, _, flags = self.predict(recs)
        return [
            {"id": r["id"], "cls": p.cls, "prob": p.prob, "final": d.cls}
            for r, p, d in zip(recs, preds, self.decisions(recs, preds, flags))
        ]

    def undo(self) -> int:
        """Remove every label under our prefix from every message carrying one. Returns messages touched."""
        self._refresh_labels(force=True)
        ours = self._ours()
        per_msg: dict[str, set[str]] = {}
        for lid in sorted(ours):
            # include SPAM/TRASH: a labeled message the user later trashed still carries our label
            for mid in self.client.list_message_ids(
                label_ids=[lid], max_results=1_000_000, include_spam_trash=True
            ):
                per_msg.setdefault(mid, set()).add(lid)
        if not self.cfg.dry_run:
            self.store.clear_applied()  # first, so history events from our removals don't read as feedback
            for mid, lids in per_msg.items():
                self.client.modify(mid, remove=sorted(lids))
        log.info("undo%s: %d messages", " [dry-run]" if self.cfg.dry_run else "", len(per_msg))
        return len(per_msg)


def _sent_ids(events: list[HistoryEvent]) -> list[str]:
    """Messages that history shows arriving in, or moving to, SENT."""
    return list(
        dict.fromkeys(
            e.message_id
            for e in events
            if (e.kind == "added" and "SENT" in (e.label_ids or e.message_label_ids))
            or (e.kind == "label_added" and "SENT" in e.label_ids)
        )
    )


def _pick(classes: set[str], added_order: list[str]) -> str:
    for c in reversed(added_order):
        if c in classes:
            return c
    return min(classes)


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _counts() -> dict[str, Any]:
    return {
        "events": 0,
        "new": 0,
        "fetched": 0,
        "labeled": 0,
        "recorded": 0,
        "skipped": 0,
        "already_decided": 0,
        "feedback": 0,
        "teacher": 0,
        "contacts_added": 0,
        "by_class": {},
        "skip_reasons": {},
    }
