"""LabelMap: our classes <-> Gmail label ids under `<prefix>/`.

Gmail label names collide case-insensitively ("Email-Town-Labeler/Orders" blocks "email-town-labeler/orders"),
so all lookups are by lower-cased name and an existing differently-cased label is reused.
"""

from __future__ import annotations

import logging

from .client import GmailClient, _status

log = logging.getLogger("email_town_labeler.gmail")


class LabelMap:
    def __init__(self, client: GmailClient, prefix: str, names: dict[str, str]):
        self.client = client
        self.prefix = prefix.strip().strip("/")
        self.names = dict(names)  # class -> display name
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

    def _ensure_name(self, name: str) -> str:
        if (lb := self._labels.get(name.lower())) is not None:
            return lb["id"]
        try:
            lb = self.client.create_label(name)
            log.info("created label %s", name)  # our own label name, not mail content
        except Exception as e:
            if _status(e) != 409:  # 409: someone else created it (or a case variant) meanwhile
                raise
            self.refresh()
            if (lb := self._labels.get(name.lower())) is None:
                raise
            return lb["id"]
        self._labels[lb["name"].lower()] = {
            "id": lb["id"],
            "name": lb["name"],
            "type": lb.get("type", "user"),
        }
        return lb["id"]

    def ensure(self, cls: str) -> str:
        """Label id for cls, creating `<prefix>` and `<prefix>/<name>` if missing."""
        key = self._key(cls)
        if (lb := self._labels.get(key)) is not None:
            return lb["id"]
        self._ensure_name(self.prefix)
        return self._ensure_name(f"{self.prefix}/{self.names[cls]}")

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
