"""The app icon (docs/brand.md): scripts/build_desktop_brand.py draws it; the committed outputs must agree.

Rendering needs Xcode, so CI cannot rebuild the PNG, ICNS and Assets.car; it checks what each must contain
and that the committed vector sources are exactly what the generator draws today.
"""

from __future__ import annotations

import importlib.util
import json
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "email_town_labeler" / "desktop" / "web"
ICON = ROOT / "brand" / "AppIcon.icon"

spec = importlib.util.spec_from_file_location(
    "build_desktop_brand", ROOT / "scripts" / "build_desktop_brand.py"
)
brand = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brand)


def test_the_committed_sources_are_what_the_generator_draws(tmp_path):
    """An edit to the committed layers or logo.svg that the script does not make would be lost on the next run."""
    written = brand.write_sources(tmp_path)
    assert len(written) == 5
    for path in written:
        committed = ROOT / path.relative_to(tmp_path)
        assert committed.read_bytes() == path.read_bytes(), f"{committed} differs from the generator"


def test_the_icon_document_names_its_layers_and_a_dark_look():
    doc = json.loads((ICON / "icon.json").read_text())
    assert doc["color-space-for-untagged-svg-colors"] == "display-p3"  # the only value Icon Composer accepts
    names = [layer["image-name"] for group in doc["groups"] for layer in group["layers"]]
    assert names == ["seal.svg", "flap.svg", "body.svg"]  # front first: the seal sits on the flap
    for name in names:
        assert (ICON / "Assets" / name).is_file(), name
    assert any(s.get("appearance") == "dark" for s in doc["fill-specializations"])


def test_colours_reach_icon_composer_as_the_same_colour_in_display_p3():
    assert brand.to_p3("#ff0000") == "#ea3323"  # sRGB red, in Display P3
    assert brand.to_p3("#ffffff") == "#ffffff" and brand.to_p3("#000000") == "#000000"
    assert brand.to_p3(brand.WAX[0]) in (ICON / "Assets" / "seal.svg").read_text()
    assert brand.WAX[0] in (WEB / "logo.svg").read_text()  # the browser draws sRGB


def test_the_mac_bundle_carries_the_glass_icon_and_a_fallback():
    spec_text = (ROOT / "scripts" / "email-town.spec").read_text()
    assert '"CFBundleIconName": "AppIcon"' in spec_text
    assert '("Assets.car", str(ROOT / "brand" / "Assets.car"), "DATA")' in spec_text
    assert (ROOT / "brand" / "Assets.car").read_bytes()[:8] == b"BOMStore"  # a compiled asset catalogue
    assert '"brand",' in (ROOT / "pyproject.toml").read_text()  # the sdist carries what these tests read


def test_the_icns_has_every_size_finder_and_older_macos_ask_for():
    data = (WEB / "app-icon.icns").read_bytes()
    assert data[:4] == b"icns" and struct.unpack(">I", data[4:8])[0] == len(data)
    types, pos = set(), 8
    while pos < len(data):
        types.add(data[pos : pos + 4].decode())
        pos += struct.unpack(">I", data[pos + 4 : pos + 8])[0]
    # 16, 32, 128, 256, 512 at 1x and 2x
    assert {"ic04", "ic05", "ic07", "ic08", "ic09", "ic10", "ic11", "ic12", "ic13", "ic14"} <= types


def test_the_window_icon_is_a_1024_pixel_png():
    data = (WEB / "app-icon.png").read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", data[16:24]) == (1024, 1024)


def test_the_header_mark_is_one_self_contained_svg():
    root = ET.fromstring((WEB / "logo.svg").read_text())
    assert root.tag == "{http://www.w3.org/2000/svg}svg" and root.get("viewBox") == "0 0 1024 1024"
    assert root.find("{http://www.w3.org/2000/svg}title").text == "Email Town"
    hrefs = [v for el in root.iter() for k, v in el.attrib.items() if k.endswith("href")]
    assert all(v.startswith("#") for v in hrefs)  # nothing fetched: the page's CSP allows only 'self'
