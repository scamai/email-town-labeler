"""Render the authored vector logo into reproducible macOS launcher resources.

Requires macOS iconutil and Playwright Chromium. Run:
uv run --with playwright python scripts/build_desktop_brand.py
"""

import subprocess
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    web = Path(__file__).resolve().parents[1] / "src/email_town_labeler/desktop/web"
    svg = (web / "logo.svg").read_text()
    with tempfile.TemporaryDirectory(prefix="email-town-brand-") as directory, sync_playwright() as p:
        iconset = Path(directory) / "EmailTown.iconset"
        iconset.mkdir()
        browser = p.chromium.launch()
        page = browser.new_page(device_scale_factor=1)
        for size in (16, 32, 64, 128, 256, 512, 1024):
            page.set_viewport_size({"width": size, "height": size})
            page.set_content(
                f"<style>html,body{{margin:0;background:transparent}}svg{{display:block;width:100vw;height:100vh}}</style>{svg}"
            )
            png = page.screenshot(omit_background=True)
            if size in (16, 32, 128, 256, 512):
                (iconset / f"icon_{size}x{size}.png").write_bytes(png)
            if size in (32, 64, 256, 512, 1024):
                half = size // 2
                (iconset / f"icon_{half}x{half}@2x.png").write_bytes(png)
            if size == 1024:
                (web / "app-icon.png").write_bytes(png)
        browser.close()
        subprocess.run(["iconutil", "-c", "icns", "-o", str(web / "app-icon.icns"), str(iconset)], check=True)
    print("Built app-icon.png and app-icon.icns from logo.svg")


if __name__ == "__main__":
    main()
