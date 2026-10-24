"""Generate the README illustrations in docs/assets/ (light and dark variants of each).

    python scripts/make_readme_art.py

Everything shown is made up: senders, subjects and snippets are fictional, and no real mail was used.
Label colors come from taxonomy.DEFAULT_LABEL_COLORS, so the art matches what the tool applies in Gmail.
"""

from __future__ import annotations

import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from email_town_labeler.taxonomy import DEFAULT_LABEL_COLORS

OUT = ROOT / "docs" / "assets"
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
THEMES = {
    "light": {
        "bg": "#ffffff",
        "panel": "#f6f8fc",
        "card": "#ffffff",
        "line": "#e3e6ec",
        "ink": "#1f1f1f",
        "ink2": "#5f6368",
        "ink3": "#80868b",
        "accent": "#1a73e8",
        "chrome": "#eef1f6",
        "dot": "#d5d9e0",
        "glow": "#e8f0fe",
        "ok": "#188038",
    },
    "dark": {
        "bg": "#0d1117",
        "panel": "#161b22",
        "card": "#1c2128",
        "line": "#30363d",
        "ink": "#e6edf3",
        "ink2": "#9da7b3",
        "ink3": "#7d8590",
        "accent": "#58a6ff",
        "chrome": "#21262d",
        "dot": "#3d444d",
        "glow": "#132339",
        "ok": "#3fb950",
    },
}
# (sender, subject, snippet, class, time) -- all fictional
ROWS = [
    (
        "Maya Ortiz",
        "Can you sign the lease renewal by Friday?",
        "The landlord needs both signatures before…",
        "action_needed",
        "9:41 AM",
    ),
    (
        "Northwind Air",
        "Check-in is open for your flight to Lisbon",
        "Seat 14C · Gate info 3 hours before…",
        "travel",
        "9:12 AM",
    ),
    (
        "The Weekly Byte",
        "Issue #212: small models, big inboxes",
        "This week: why on-device AI is having a…",
        "newsletters",
        "8:30 AM",
    ),
    (
        "Lumen Shoes",
        "48 hours only: 30% off everything",
        "Our biggest sale of the season starts…",
        "promotional",
        "7:55 AM",
    ),
    (
        "Parcel & Co.",
        "Your order #A-1042 has shipped",
        "Arriving Thursday · Track your package…",
        "orders",
        "7:20 AM",
    ),
    (
        "Acme Cloud",
        "New sign-in to your account",
        "We noticed a sign-in from a new device…",
        "updates",
        "6:48 AM",
    ),
    (
        "Sam Rivera",
        "Hi! We met at the design meetup",
        "Great chatting on Tuesday. I wanted to…",
        "new_sender",
        "Yesterday",
    ),
]
NAMES = {"action_needed": "action needed", "new_sender": "new sender"}


def chip(x: float, y: float, cls: str, prefix: str = "e/", size: float = 12) -> tuple[str, float]:
    c = DEFAULT_LABEL_COLORS[cls]
    text = prefix + NAMES.get(cls, cls)
    w = len(text) * size * 0.56 + 14
    svg = (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{size + 8}" rx="4" fill="{c["backgroundColor"]}"/>'
        f'<text x="{x + 7:.1f}" y="{y + size + 2.5:.1f}" font-size="{size}" font-weight="600" '
        f'fill="{c["textColor"]}">{escape(text)}</text>'
    )
    return svg, w


def banner(t: dict) -> str:
    chips, x = [], 0.0
    for cls in ["action_needed", "travel", "newsletters", "promotional", "orders", "updates", "new_sender"]:
        s, w = chip(x, 0, cls, prefix="", size=13)
        chips.append(s)
        x += w + 8
    off = (880 - (x - 8)) / 2
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="880" height="250" viewBox="0 0 880 250" font-family="{FONT}">
<title>email-town-labeler: your Gmail, sorted on your own machine</title>
<rect width="880" height="250" rx="16" fill="{t["panel"]}"/>
<g transform="translate(372 30)">
  <rect width="136" height="92" rx="14" fill="{t["card"]}" stroke="{t["line"]}" stroke-width="2"/>
  <path d="M14 22 L68 58 L122 22" fill="none" stroke="{t["ink2"]}" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
  <g transform="translate(86 52) rotate(-18)">
    <path d="M0 0 h34 l12 12 l-12 12 h-34 z" fill="{DEFAULT_LABEL_COLORS["action_needed"]["backgroundColor"]}"/>
    <circle cx="7" cy="12" r="3.5" fill="{t["card"]}"/>
  </g>
</g>
<text x="440" y="160" text-anchor="middle" font-size="34" font-weight="700" fill="{t["ink"]}">email-town-labeler</text>
<text x="440" y="190" text-anchor="middle" font-size="17" fill="{t["ink2"]}">Your Gmail, sorted by an AI that runs on your own computer.</text>
<g transform="translate({off:.1f} 210)">{"".join(chips)}</g>
</svg>
'''


def inbox(t: dict) -> str:
    W, top, rh = 880, 92, 58
    H = top + rh * len(ROWS) + 20
    rows = []
    for i, (who, subj, snip, cls, when) in enumerate(ROWS):
        y = top + i * rh
        s, cw = chip(0, 0, cls)
        delay = 0.3 + i * 0.6
        bold = "700" if i < 3 else "500"
        rows.append(f'''<g transform="translate(0 {y})">
  <rect x="20" y="0" width="{W - 40}" height="{rh}" fill="{t["card"]}"/>
  <line x1="20" y1="{rh}" x2="{W - 20}" y2="{rh}" stroke="{t["line"]}"/>
  <circle cx="44" cy="{rh / 2}" r="5" fill="{t["accent"] if i < 3 else t["dot"]}"/>
  <text x="62" y="{rh / 2 + 5}" font-size="14" font-weight="{bold}" fill="{t["ink"]}">{escape(who)}</text>
  <rect class="pulse" style="animation-delay:{delay:.2f}s" x="192" y="{rh / 2 - 14}" width="{cw + 8:.1f}" height="28" rx="7" fill="{DEFAULT_LABEL_COLORS[cls]["backgroundColor"]}"/>
  <g transform="translate(196 {rh / 2 - 10})">{s}</g>
  <text x="{196 + cw + 10:.0f}" y="{rh / 2 + 5}" font-size="14" fill="{t["ink"]}"><tspan font-weight="{bold}">{escape(subj)}</tspan></text>
  <text x="{W - 40}" y="{rh / 2 + 5}" text-anchor="end" font-size="12" fill="{t["ink3"]}">{escape(when)}</text>
</g>''')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="{FONT}">
<title>A Gmail inbox where each new email gets one colored label (illustration with made-up emails)</title>
<style>
  .pulse {{ opacity: 0; animation: pulse 6s ease-out infinite; }}
  @keyframes pulse {{ 0% {{ opacity: 0; }} 4% {{ opacity: 0.55; }} 14%, 100% {{ opacity: 0; }} }}
  @media (prefers-reduced-motion: reduce) {{ .pulse {{ animation: none; }} }}
</style>
<rect width="{W}" height="{H}" rx="14" fill="{t["panel"]}"/>
<rect x="20" y="16" width="{W - 40}" height="40" rx="20" fill="{t["chrome"]}"/>
<circle cx="44" cy="36" r="7" fill="none" stroke="{t["ink3"]}" stroke-width="2"/>
<line x1="49" y1="41" x2="54" y2="46" stroke="{t["ink3"]}" stroke-width="2" stroke-linecap="round"/>
<text x="66" y="41" font-size="14" fill="{t["ink3"]}">Search mail</text>
<text x="{W - 40}" y="41" text-anchor="end" font-size="12" fill="{t["ok"]}">● labeling on this computer</text>
<text x="24" y="80" font-size="13" font-weight="600" fill="{t["ink2"]}">Inbox</text>
{"".join(rows)}
</svg>
'''


def flow(t: dict) -> str:
    def node(x, title, sub, hl=False):
        return (
            f'<rect x="{x}" y="40" width="220" height="96" rx="14" fill="{t["glow"] if hl else t["card"]}" '
            f'stroke="{t["accent"] if hl else t["line"]}" stroke-width="2"/>'
            f'<text x="{x + 110}" y="80" text-anchor="middle" font-size="17" font-weight="700" fill="{t["ink"]}">{title}</text>'
            f'<text x="{x + 110}" y="106" text-anchor="middle" font-size="13" fill="{t["ink2"]}">{sub}</text>'
        )

    def arrow(x1, x2, y, label):
        return (
            f'<line x1="{x1}" y1="{y}" x2="{x2 - 8}" y2="{y}" stroke="{t["ink3"]}" stroke-width="2"/>'
            f'<path d="M{x2 - 10} {y - 6} L{x2} {y} L{x2 - 10} {y + 6} z" fill="{t["ink3"]}"/>'
            f'<text x="{(x1 + x2) / 2}" y="{y - 10}" text-anchor="middle" font-size="12" fill="{t["ink2"]}">{label}</text>'
        )

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="880" height="220" viewBox="0 0 880 220" font-family="{FONT}">
<title>How it works: Gmail, then a model on your computer, then a label back in Gmail</title>
<rect width="880" height="220" rx="14" fill="{t["panel"]}"/>
{node(24, "Gmail", "a new email arrives")}
{node(330, "Your computer", "a local AI model reads it", True)}
{node(636, "Gmail", "one colored label is added")}
{arrow(244, 330, 76, "every 60 s")}
{arrow(550, 636, 76, "one label")}
<path d="M746 136 C 746 176, 440 176, 440 144" fill="none" stroke="{t["ink3"]}" stroke-width="2" stroke-dasharray="5 5"/>
<path d="M434 152 L440 140 L446 152 z" fill="{t["ink3"]}"/>
<text x="600" y="190" text-anchor="middle" font-size="12" fill="{t["ink2"]}">you fix a label → it learns, on your computer</text>
<text x="440" y="208" text-anchor="middle" font-size="12" font-weight="600" fill="{t["ok"]}">Your email never goes to a third-party server.</text>
</svg>
'''


def chip_file(cls: str) -> str:
    body, w = chip(0.5, 0.5, cls, prefix="", size=12)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w + 1:.0f}" height="21" viewBox="0 0 {w + 1:.0f} 21" '
        f'font-family="{FONT}"><title>{escape(NAMES.get(cls, cls))}</title>{body}</svg>\n'
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for cls in DEFAULT_LABEL_COLORS:
        (OUT / f"chip-{cls.replace('_', '-')}.svg").write_text(chip_file(cls), encoding="utf-8")
        print(f"wrote docs/assets/chip-{cls.replace('_', '-')}.svg")
    for name, fn in (("banner", banner), ("inbox", inbox), ("how-it-works", flow)):
        for theme, t in THEMES.items():
            (OUT / f"{name}-{theme}.svg").write_text(fn(t), encoding="utf-8")
            print(f"wrote docs/assets/{name}-{theme}.svg")


if __name__ == "__main__":
    main()
