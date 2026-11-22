"""Draw the Email Town mark and build every icon the apps use from it (docs/brand.md).

The mark: a letter closed with a wax seal, the seal pressed with a little house (the town). This script is
its source. It writes the Icon Composer document brand/AppIcon.icon and the flat header mark logo.svg, then
uses Apple's tools to build the rest:

  brand/AppIcon.icon                 Icon Composer document (layers + icon.json), open it to preview
  brand/Assets.car                   compiled by actool: the macOS 26+ icon with light, dark and tinted looks
  src/.../desktop/web/app-icon.icns  every size, Apple's margin: macOS 14-15 and Finder (CFBundleIconFile)
  src/.../desktop/web/app-icon.png   1024 px: the Windows .ico and the Linux window icon
  src/.../desktop/web/logo.svg       the mark in the workbench header and the browser tab

Rendering needs macOS with Xcode 26 or later (Icon Composer's ictool, actool, iconutil) and Pillow:
    uv run --with pillow python scripts/build_desktop_brand.py
`--sources-only` writes the two source sets and nothing else (any OS). Inspect the 16 and 32 px images in
the iconset it prints before committing; tests/test_desktop_brand.py checks the sources match this file.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ICON = ROOT / "brand" / "AppIcon.icon"
CAR = ROOT / "brand" / "Assets.car"
WEB = ROOT / "src" / "email_town_labeler" / "desktop" / "web"

# ─── Palette (sRGB) ───
# Chosen by the owner on 2026-11-21 from the system's tinted rendering of a teal-and-kraft study.
BG = ("#7b67d8", "#5f4dc0")  # tile, top to bottom
BG_DARK = ("#2b2253", "#16112f")
PAPER = ("#f7f5ff", "#e5dfff")
FLAP = ("#fcfbff", "#efebff")
EDGE = "#b3a6ec"  # the flap's V: it must stay visible at 16 px, or the letter reads as a card with a dot
WAX = ("#a08ff0", "#7462d2")
DEBOSS = "#5643b0"

# ─── Geometry, on Icon Composer's 1024 x 1024 canvas (the full squircle) ───
C = 512
X0, X1, Y0, Y1 = 148, 876, 292, 772  # envelope
TIP = (C, 600)  # point of the flap, where the seal sits
SEAL_R = 80
MARGIN = 100  # Apple's macOS template: an 824 px squircle centred in 1024


def rounded_poly(points, r):
    """Closed polygon whose corners are rounded by r (one radius, or one per corner)."""
    n = len(points)
    radii = r if isinstance(r, list) else [r] * n
    corners = []
    for i in range(n):
        p0, p1, p2 = points[i - 1], points[i], points[(i + 1) % n]
        v1 = (p0[0] - p1[0], p0[1] - p1[1])
        v2 = (p2[0] - p1[0], p2[1] - p1[1])
        l1, l2 = math.hypot(*v1), math.hypot(*v2)
        angle = math.acos(max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (l1 * l2))))
        d = min(radii[i] / math.tan(angle / 2) if angle > 1e-6 else 0, l1 / 2, l2 / 2)
        a = (p1[0] + v1[0] / l1 * d, p1[1] + v1[1] / l1 * d)
        b = (p1[0] + v2[0] / l2 * d, p1[1] + v2[1] / l2 * d)
        corners.append((a, p1, b))
    path = f"M{corners[0][2][0]:.1f},{corners[0][2][1]:.1f}"
    for a, p, b in corners[1:] + corners[:1]:
        path += f" L{a[0]:.1f},{a[1]:.1f} Q{p[0]:.1f},{p[1]:.1f} {b[0]:.1f},{b[1]:.1f}"
    return path + " Z"


def wax_outline(cx, cy, radius, n=96):
    """A circle whose radius ripples a little, as poured wax does (Catmull-Rom through n points)."""
    lobes = ((7, 0.03, 0.0), (11, 0.018, 1.3), (17, 0.01, 0.4))
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        r = radius * (1 + sum(amp * math.sin(k * a + phase) for k, amp, phase in lobes))
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    path = f"M{pts[0][0]:.1f},{pts[0][1]:.1f}"
    for i in range(n):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[(i + 1) % n], pts[(i + 2) % n]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        path += f" C{c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} {p2[0]:.1f},{p2[1]:.1f}"
    return path + " Z"


def squircle(size=1024.0, n=5.0, steps=128):
    """Apple's continuous-corner tile, as a superellipse; only the flat logo.svg draws it itself."""
    h = size / 2
    pts = []
    for i in range(steps):
        t = 2 * math.pi * i / steps
        c, s = math.cos(t), math.sin(t)
        pts.append((h + h * math.copysign(abs(c) ** (2 / n), c), h + h * math.copysign(abs(s) ** (2 / n), s)))
    return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + " Z"


def gradient(id_, top, bottom):
    return (
        f'<linearGradient id="{id_}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{top}"/><stop offset="1" stop-color="{bottom}"/></linearGradient>'
    )


# ─── The three layers: (defs, body) each, drawn back to front ───


def envelope_layer():
    body = rounded_poly([(X0, Y0), (X1, Y0), (X1, Y1), (X0, Y1)], 48)
    return gradient("paper", *PAPER), f'<path d="{body}" fill="url(#paper)"/>'


def flap_layer():
    # The flap shares the envelope's outline exactly: a shape clipped by the envelope, so no seam shows.
    body = rounded_poly([(X0, Y0), (X1, Y0), (X1, Y1), (X0, Y1)], 48)
    flap = rounded_poly(
        [(X0 - 40, Y0 - 20), TIP, (X1 + 40, Y0 - 20), (X1 + 40, Y0 - 200), (X0 - 40, Y0 - 200)],
        [0, 26, 0, 0, 0],
    )
    vee = (
        f'<path d="M{X0 + 6},{Y0 + 4} L{TIP[0]},{TIP[1] - 8} L{X1 - 6},{Y0 + 4}" fill="none" stroke="{EDGE}" '
        'stroke-width="12" stroke-linecap="round" stroke-linejoin="round"/>'
    )
    defs = gradient("flap", *FLAP) + f'<clipPath id="envelope"><path d="{body}"/></clipPath>'
    return defs, f'<g clip-path="url(#envelope)"><path d="{flap}" fill="url(#flap)"/>{vee}</g>'


def seal_layer():
    cy = TIP[1] - 14
    seal = wax_outline(C, cy, SEAL_R)
    hw, base, eaves, roof = 28, cy + 33, cy - 5, 36  # a gable house pressed into the wax, its door open
    house = rounded_poly(
        [(C - hw, base), (C - hw, eaves), (C, eaves - roof), (C + hw, eaves), (C + hw, base)], [3, 3, 7, 3, 3]
    )
    body = (
        f'<path d="{seal}" fill="url(#wax)"/>'
        f'<circle cx="{C}" cy="{cy}" r="{SEAL_R * 0.74:.1f}" fill="none" stroke="{DEBOSS}" stroke-opacity=".5" '
        'stroke-width="6"/>'
        f'<path d="{house}" fill="{DEBOSS}"/>'
        f'<rect x="{C - 9}" y="{base - 28}" width="18" height="28" rx="5" fill="url(#wax)"/>'
    )
    return gradient("wax", *WAX), body


LAYERS = (
    ("Envelope", "body.svg", envelope_layer),
    ("Flap", "flap.svg", flap_layer),
    ("Seal", "seal.svg", seal_layer),
)


# ─── Colour: Icon Composer reads untagged SVG colours as Display P3 ───


def _decode(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _encode(c):
    c = max(0.0, min(1.0, c))
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


SRGB_TO_P3 = ((0.8224621, 0.1775380, 0.0), (0.0331941, 0.9668058, 0.0), (0.0170827, 0.0723974, 0.9105199))


def to_p3(hex_):
    """The Display-P3 hex that shows the same colour as this sRGB hex."""
    rgb = [_decode(int(hex_[i : i + 2], 16) / 255) for i in (1, 3, 5)]
    out = [_encode(sum(SRGB_TO_P3[k][j] * rgb[j] for j in range(3))) for k in range(3)]
    return "#" + "".join(f"{round(v * 255):02x}" for v in out)


def p3_colours(svg_text):
    return re.sub(r"#[0-9a-fA-F]{6}\b", lambda m: to_p3(m.group(0)), svg_text)


def srgb_value(hex_):
    r, g, b = (int(hex_[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return f"srgb:{r:.5f},{g:.5f},{b:.5f},1.00000"


# ─── Sources ───


def svg_document(defs, body, size=1024):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 1024 1024">'
        f"<defs>{defs}</defs>{body}</svg>\n"
    )


def icon_json():
    groups = [
        {
            "layers": [{"glass": True, "image-name": name, "name": name.removesuffix(".svg")}],
            "name": group,
            "shadow": {"kind": "neutral", "opacity": 0.5},
            "translucency": {"enabled": False, "value": 0.5},
        }
        for group, name, _draw in reversed(LAYERS)  # Icon Composer lists the front group first
    ]
    return {
        "color-space-for-untagged-svg-colors": "display-p3",
        "fill-specializations": [
            {"value": {"linear-gradient": [srgb_value(BG[0]), srgb_value(BG[1])]}},
            {
                "appearance": "dark",
                "value": {"linear-gradient": [srgb_value(BG_DARK[0]), srgb_value(BG_DARK[1])]},
            },
        ],
        "groups": groups,
        "supported-platforms": {"circles": ["watchOS"], "squares": "shared"},
    }


def logo_svg():
    """The flat mark for the header and the browser tab: the same layers on a drawn tile, in sRGB."""
    defs = gradient("tile", *BG) + '<filter id="lift" x="-10%" y="-10%" width="120%" height="130%">'
    defs += (
        '<feDropShadow dx="0" dy="10" stdDeviation="12" flood-color="#1d1450" flood-opacity=".28"/></filter>'
    )
    body = f'<title>Email Town</title><path d="{squircle()}" fill="url(#tile)"/><g filter="url(#lift)">'
    for _group, _name, draw in LAYERS:
        layer_defs, layer_body = draw()
        defs += layer_defs
        body += layer_body
    return svg_document(defs, body + "</g>", size=64)


def write_sources(root: Path = ROOT) -> list[Path]:
    """Write the Icon Composer document and logo.svg under root; return the files written."""
    icon, web = root / ICON.relative_to(ROOT), root / WEB.relative_to(ROOT)
    if icon.exists():
        shutil.rmtree(icon)
    (icon / "Assets").mkdir(parents=True)
    written = []
    for _group, name, draw in LAYERS:
        path = icon / "Assets" / name
        path.write_text(p3_colours(svg_document(*draw())))
        written.append(path)
    (icon / "icon.json").write_text(json.dumps(icon_json(), indent=2) + "\n")
    web.mkdir(parents=True, exist_ok=True)
    (web / "logo.svg").write_text(logo_svg())
    return written + [icon / "icon.json", web / "logo.svg"]


# ─── Rendering (macOS, Xcode 26+) ───


def ictool() -> str:
    found = os.environ.get("ICTOOL") or next(
        iter(
            sorted(
                glob.glob(
                    "/Applications/Xcode*.app/Contents/Applications/Icon Composer.app/Contents/Executables/ictool"
                )
            )
        ),
        "",
    )
    if not found:
        sys.exit("Icon Composer's ictool was not found: install Xcode 26 or later, or set ICTOOL")
    return found


def render(tool: str, out: Path, rendition="Default", size=1024):
    subprocess.run(
        [tool, str(ICON), "--export-image", "--output-file", str(out), "--platform", "macOS",
         "--rendition", rendition, "--width", str(size), "--height", str(size), "--scale", "1"],
        check=True, capture_output=True,
    )  # fmt: skip


def build_icns(master: Path, iconset: Path):
    """Every size Finder and older macOS ask for, each with Apple's margin, from the 1024 px render."""
    from PIL import Image

    image = Image.open(master).convert("RGBA")
    iconset.mkdir()
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            px = size * scale
            inner = round(px * (1024 - 2 * MARGIN) / 1024)
            tile = Image.new("RGBA", (px, px), (0, 0, 0, 0))
            offset = (px - inner) // 2
            tile.paste(image.resize((inner, inner), Image.LANCZOS), (offset, offset))
            tile.save(iconset / f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png")
    subprocess.run(["iconutil", "-c", "icns", "-o", str(WEB / "app-icon.icns"), str(iconset)], check=True)


def compile_car(work: Path):
    out = work / "car"
    out.mkdir()
    subprocess.run(
        ["xcrun", "actool", str(ICON), "--compile", str(out), "--platform", "macosx",
         "--minimum-deployment-target", "14.0", "--app-icon", "AppIcon",
         "--output-partial-info-plist", str(work / "partial.plist")],
        check=True, capture_output=True,
    )  # fmt: skip
    shutil.copyfile(out / "Assets.car", CAR)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--sources-only", action="store_true", help="write brand/AppIcon.icon and logo.svg only"
    )
    args = parser.parse_args(argv)
    for path in write_sources():
        print("wrote", path.relative_to(ROOT))
    if args.sources_only:
        return 0
    if sys.platform != "darwin":
        sys.exit("Rendering needs macOS with Xcode 26 or later; the sources above are written")
    tool = ictool()
    work = Path(tempfile.mkdtemp(prefix="email-town-brand-"))
    master = work / "AppIcon-1024.png"
    render(tool, master)
    shutil.copyfile(master, WEB / "app-icon.png")
    build_icns(master, work / "AppIcon.iconset")
    compile_car(work)
    for name in ("app-icon.png", "app-icon.icns"):
        print("wrote", (WEB / name).relative_to(ROOT))
    print("wrote", CAR.relative_to(ROOT))
    print(f"inspect the small sizes before committing: {work / 'AppIcon.iconset'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
