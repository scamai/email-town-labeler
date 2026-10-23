"""The label set. Six content classes plus `unknown` (no label fits) are what models predict.

`new_sender` is not a model class: the engine applies it after the model, to `unknown` mail in Gmail's
Primary category from an address you have never written to (see policy.py and engine.py).
"""

CONTENT_LABELS = ["action_needed", "newsletters", "promotional", "orders", "travel", "updates"]
UNKNOWN = "unknown"
CLASSES = CONTENT_LABELS + [UNKNOWN]  # what a model predicts; never extended by post-model rules
NEW_SENDER = "new_sender"
LABEL_CLASSES = CLASSES + [NEW_SENDER]  # every class the engine can decide and LabelMap can map

# Default Gmail display names, shown as "<prefix>/<name>". Users can override in config.
DISPLAY_NAMES = {
    "action_needed": "action needed",
    "newsletters": "newsletters",
    "promotional": "promotional",
    "orders": "orders",
    "travel": "travel",
    "updates": "updates",
    "unknown": "unknown",
    "new_sender": "new sender",
}

DESCRIPTIONS = {
    "action_needed": "asks the recipient to do something: reply, approve, pay, sign, review or decide",
    "newsletters": "a newsletter, digest or blog post the recipient subscribed to",
    "promotional": "unsolicited marketing, sales outreach or a promotional offer",
    "orders": "a receipt, invoice, order confirmation or shipping update",
    "travel": "travel: flights, hotels, rentals, itineraries or check-in notices",
    "updates": "an automated notification from an app or service: account, security or status change",
    "unknown": "none of these: personal or work mail that needs no category",
    "new_sender": "personal mail (Primary, no other label) from an address you have never written to",
}

# Default Gmail label colors per class, matching the colors Town gives its own labels. Values must
# come from Gmail's fixed label palette (labels.create/patch reject anything else with a 400).
# `unknown` has no Town counterpart and stays uncolored, as does the `<prefix>` parent label.
# Users can override or disable these in config ([label_colors], [gmail] label_colors).
DEFAULT_LABEL_COLORS: dict[str, dict[str, str]] = {
    "action_needed": {"backgroundColor": "#ffad47", "textColor": "#000000"},
    "newsletters": {"backgroundColor": "#16a766", "textColor": "#ffffff"},
    "promotional": {"backgroundColor": "#f691b3", "textColor": "#000000"},
    "orders": {"backgroundColor": "#b99aff", "textColor": "#000000"},
    "travel": {"backgroundColor": "#a4c2f4", "textColor": "#000000"},
    "updates": {"backgroundColor": "#fad165", "textColor": "#000000"},
    "new_sender": {"backgroundColor": "#4a86e8", "textColor": "#ffffff"},
}
