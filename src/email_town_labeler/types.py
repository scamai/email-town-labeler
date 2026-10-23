"""Shared data shapes. Plain dicts/dataclasses so every layer can be tested without Google."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypedDict


class EmailRecord(TypedDict, total=False):
    id: str
    thread_id: str
    label_ids: list[str]
    internal_date: int  # ms since epoch
    history_id: str
    size_estimate: int
    snippet: str
    headers: dict[str, str]  # lower-case header name -> value
    body_text: str
    body_source: str  # "plain" | "html" | "none"
    has_calendar: bool
    attachments: list[dict]


@dataclass
class Prediction:
    cls: str
    prob: float
    probs: dict[str, float] = field(default_factory=dict)
    # the model's raw scores over CLASSES (same order), when it has them; learn.py stores these
    logits: list[float] | None = None


@dataclass
class HistoryEvent:
    kind: Literal["added", "label_added", "label_removed"]
    message_id: str
    label_ids: list[str] = field(default_factory=list)  # labels added/removed by this event
    message_label_ids: list[str] = field(default_factory=list)  # message labels after the event
