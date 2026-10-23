"""Deterministic made-up mail for parity checks and timing (never real messages).

`fake_records(n)` cycles through templates for every class, with bodies from a few words to a few
thousand characters so checks cover short inputs, padding and truncation at max_length.
"""

from __future__ import annotations

import random

TEMPLATES = [
    (
        "Example Shop <orders@shop.example>",
        "Your order #{n} has shipped",
        "Your order of {k} widgets has shipped. Track it with code ZX{n}.",
        False,
        False,
    ),
    (
        "Alex Doe <alex@corp.example>",
        "Can you review the Q{k} budget by Friday?",
        "Could you look over the attached budget and sign off before Friday?",
        False,
        False,
    ),
    (
        "Widget Weekly <news@list.example>",
        "This week in widgets, issue {n}",
        "Ten stories about widgets, gears and sprockets from around the made-up web.",
        True,
        False,
    ),
    (
        "Mega Deals <deals@promo.example>",
        "{k}0% off everything this weekend only",
        "Do not miss our biggest sale of the season. Use code SAVE{k} at checkout.",
        True,
        False,
    ),
    (
        "Fly Example <noreply@air.example>",
        "Your boarding pass for flight EX{n}",
        "Gate {k}, boarding at 10:{k}5. Seat {k}A. Have a pleasant made-up trip.",
        False,
        True,
    ),
    (
        "Cloud Service <alerts@service.example>",
        "Security alert: new sign-in on device {k}",
        "We noticed a new sign-in to your account. If this was you, no action is needed.",
        False,
        False,
    ),
    (
        "Sam Smith <sam@friends.example>",
        "Dinner on Saturday?",
        "Hey, are you around this weekend? We were thinking of trying the new place.",
        False,
        False,
    ),
    (
        "Hotel Example <stay@hotel.example>",
        "Reservation confirmed: {k} nights",
        "Check-in is from 3pm. Your confirmation number is HX{n}.",
        False,
        True,
    ),
    (
        "Billing <billing@saas.example>",
        "Invoice {n} is due",
        "Your invoice for {k} seats is attached. Payment is due in 14 days.",
        False,
        False,
    ),
    (
        "Recruiter <jobs@talent.example>",
        "Quick question about your experience",
        "I came across your profile and think you would be a great fit for a role.",
        True,
        False,
    ),
]

FILLER = (
    "This paragraph is filler text for a made-up message, written only to make the body longer. "
    "It mentions widgets, schedules, receipts, gates and newsletters in no particular order. "
)
# token-dense, like tracking links: 2,000 characters of this is well past 512 tokens
DENSE = "https://track.example/c/Ab12Cd34Ef56?u=9f8e7d6c&id=0x1A2B3C4D&utm=zz9 "


def fake_records(n: int = 50, seed: int = 0) -> list[dict]:
    rng = random.Random(seed)
    out = []
    for i in range(n):
        sender, subject, body, listy, cal = TEMPLATES[i % len(TEMPLATES)]
        k, num = rng.randint(1, 9), rng.randint(1000, 9999)
        reps = rng.choice([0, 0, 1, 3, 8, 30])
        filler = DENSE * 30 if reps >= 8 and i % 2 else FILLER * reps
        headers = {"from": sender, "subject": subject.format(n=num, k=k)}
        if listy:
            headers["list-unsubscribe"] = "<mailto:unsubscribe@list.example>"
        out.append(
            {
                "id": f"fake-{i}",
                "thread_id": f"fake-{i}",
                "label_ids": ["INBOX"],
                "internal_date": 0,
                "history_id": "1",
                "size_estimate": 1,
                "snippet": "",
                "headers": headers,
                "body_text": body.format(n=num, k=k) + " " + filler,
                "body_source": "plain",
                "has_calendar": cal,
                "attachments": [],
            }
        )
    return out
