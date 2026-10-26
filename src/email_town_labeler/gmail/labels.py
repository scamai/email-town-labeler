"""LabelMap: our classes <-> Gmail label ids under `<prefix>/`.

Gmail label names collide case-insensitively ("Email-Town-Labeler/Orders" blocks "email-town-labeler/orders"),
so all lookups are by lower-cased name and an existing differently-cased label is reused.

Colors: each class may have a Gmail-palette color ({"textColor", "backgroundColor"}). New class labels
are created with it, and `sync_colors()` patches existing class labels whose color is missing or
different. Only `<prefix>/<name>` class labels are ever colored; the `<prefix>` parent and every
label outside the prefix are never touched. A color Gmail rejects (400) is logged and skipped.

Every label under the prefix counts as ours (`undo` removes them all). `foreign_labels` finds the
ones that do not look like it (setup warns about them), and `prefix_renames` plans moving the whole
prefix to a new name in place (`labels rename-prefix`), so messages keep their labels.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from .client import GmailClient, _status

log = logging.getLogger("email_town_labeler.gmail")


def _under(name: str, prefix: str) -> bool:
    n, p = name.casefold(), prefix.casefold()
    return n == p or n.startswith(p + "/")


def _class_names(prefix: str, names: dict[str, str]) -> set[str]:
    return {f"{prefix}/{n}".casefold() for n in names.values()}


def foreign_labels(labels: Iterable[dict], prefix: str, names: dict[str, str]) -> list[dict]:
    """User labels under `prefix` other than the parent and the `<prefix>/<name>` class labels."""
    ours = _class_names(prefix, names) | {prefix.casefold()}
    return [
        lb
        for lb in labels
        if lb.get("type", "user") == "user"
        and _under(lb["name"], prefix)
        and lb["name"].casefold() not in ours
    ]


def prefix_renames(
    labels: Iterable[dict], old: str, new: str, names: dict[str, str]
) -> list[tuple[str, str]]:
    """(label id, new name) for every user label under `old` (the parent last), moving it under `new`.

    ValueError if `new` is inside `old` (or the reverse), or if a label under `new` is in the way:
    anything but a class label that an interrupted earlier rename already moved."""
    labels = [lb for lb in labels if lb.get("type", "user") == "user"]
    if old.casefold() != new.casefold() and (_under(new, old) or _under(old, new)):
        raise ValueError(f"'{new}' and '{old}' are inside one another")
    moving = [lb for lb in labels if _under(lb["name"], old)]
    if old == new or not moving:
        return []
    plan = [(lb["id"], new + lb["name"][len(old) :]) for lb in moving]
    targets = {name.casefold() for _, name in plan}
    ids = {lb["id"] for lb in moving}
    leftover = _class_names(new, names)
    blocking = [
        lb["name"]
        for lb in labels
        if lb["id"] not in ids
        and _under(lb["name"], new)
        and (lb["name"].casefold() in targets or lb["name"].casefold() not in leftover)
    ]
    if blocking:
        raise ValueError(f"{len(blocking)} label(s) under '{new}/' already exist")
    return sorted(plan, key=lambda m: -m[1].count("/"))  # children first: the parent goes last


def _norm_color(color: dict | None) -> dict[str, str] | None:
    """{"textColor", "backgroundColor"} lower-cased, or None when unset/incomplete."""
    if not color or not color.get("textColor") or not color.get("backgroundColor"):
        return None
    return {
        "textColor": str(color["textColor"]).lower(),
        "backgroundColor": str(color["backgroundColor"]).lower(),
    }


class LabelMap:
    def __init__(
        self,
        client: GmailClient,
        prefix: str,
        names: dict[str, str],
        colors: dict[str, dict[str, str]] | None = None,
    ):
        self.client = client
        self.prefix = prefix.strip().strip("/")
        self.names = dict(names)  # class -> display name
        # class -> wanted color; classes without one are left as they are
        self.colors = {c: n for c, v in (colors or {}).items() if c in self.names and (n := _norm_color(v))}
        self._by_name: dict[str, dict] | None = None  # lower-cased name -> label

    def _key(self, cls: str) -> str:
        if cls not in self.names:
            raise KeyError(f"unknown class {cls!r}")
        return f"{self.prefix}/{self.names[cls]}".lower()

    def refresh(self) -> None:
        """Re-list labels from Gmail (cheap). Called lazily on first use and after a 409."""
        self._by_name = {lb["name"].lower(): lb for lb in self.client.list_labels()}

    @property
    def _labels(self) -> dict[str, dict]:
        if self._by_name is None:
            self.refresh()
        assert self._by_name is not None
        return self._by_name

    def _create(self, name: str, color: dict[str, str] | None) -> dict:
        if color is None:
            return self.client.create_label(name)
        try:
            return self.client.create_label(name, color=color)
        except Exception as e:
            if _status(e) != 400:
                raise
            log.warning(
                "Gmail rejected color %s/%s for label %s (not in its palette?); creating it uncolored",
                color["backgroundColor"],
                color["textColor"],
                name,
            )
            return self.client.create_label(name)

    def _ensure_name(self, name: str, color: dict[str, str] | None = None) -> str:
        if (lb := self._labels.get(name.lower())) is not None:
            return lb["id"]
        try:
            lb = self._create(name, color)
            log.info("created label %s", name)  # our own label name, not mail content
        except Exception as e:
            if _status(e) != 409:  # 409: someone else created it (or a case variant) meanwhile
                raise
            self.refresh()
            if (lb := self._labels.get(name.lower())) is None:
                raise
            return lb["id"]
        row = {"id": lb["id"], "name": lb["name"], "type": lb.get("type", "user")}
        if lb.get("color"):
            row["color"] = dict(lb["color"])
        self._labels[lb["name"].lower()] = row
        return lb["id"]

    def ensure(self, cls: str) -> str:
        """Label id for cls, creating `<prefix>` and `<prefix>/<name>` if missing."""
        key = self._key(cls)
        if (lb := self._labels.get(key)) is not None:
            return lb["id"]
        self._ensure_name(self.prefix)  # the parent stays uncolored
        return self._ensure_name(f"{self.prefix}/{self.names[cls]}", self.colors.get(cls))

    def sync_colors(self) -> int:
        """Patch existing class labels whose color is missing or differs from the wanted one.

        Only `<prefix>/<name>` labels of configured classes with a wanted color are considered;
        missing labels are not created (ensure() colors them on creation). A 400 (color rejected) is
        logged and skipped; other errors propagate. Returns how many labels were patched.
        """
        patched = 0
        for cls, want in self.colors.items():
            lb = self._labels.get(self._key(cls))
            if lb is None or lb.get("type", "user") != "user" or _norm_color(lb.get("color")) == want:
                continue
            try:
                self.client.set_label_color(lb["id"], want)
            except Exception as e:
                if _status(e) != 400:
                    raise
                log.warning(
                    "Gmail rejected color %s/%s for label %s (not in its palette?); leaving it as is",
                    want["backgroundColor"],
                    want["textColor"],
                    lb["name"],
                )
                continue
            lb["color"] = dict(want)
            log.info("set color of label %s", lb["name"])  # our own label name, not mail content
            patched += 1
        return patched

    def class_of(self, label_id: str) -> str | None:
        """The class whose label has this id, or None (other labels, or ours not yet created)."""
        for cls in self.names:
            lb = self._labels.get(self._key(cls))
            if lb is not None and lb["id"] == label_id:
                return cls
        return None

    def our_label_ids(self) -> set[str]:
        """Ids of every existing label under the prefix (including the parent and unmapped children)."""
        p = self.prefix.lower()
        return {lb["id"] for name, lb in self._labels.items() if name == p or name.startswith(p + "/")}
