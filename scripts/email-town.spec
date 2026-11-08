# PyInstaller spec for the Email Town desktop app: one folder (onedir) per platform.
#   macOS:   dist/Email Town.app       (Apple Silicon, macOS 14+: the onnxruntime and numpy wheels' floor)
#   Windows: dist/Email Town/Email Town.exe
#   Linux:   dist/email-town/email-town (uses the system's GTK 3 and WebKitGTK 4.1, see below)
# Build with `python scripts/build_app.py` (checks the environment, times the build, can zip it).
# The model is not bundled: the app downloads it on first use, as the CLI does.
# -*- mode: python -*-
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

from email_town_labeler import __version__

ROOT = Path(SPECPATH).parent  # noqa: F821 - SPECPATH is defined by PyInstaller
WEB = ROOT / "src" / "email_town_labeler" / "desktop" / "web"
MAC, WINDOWS = sys.platform == "darwin", sys.platform == "win32"
NAME = "email-town" if not (MAC or WINDOWS) else "Email Town"

# Model backends are imported by name (models/registry.py BUILTIN), so collect every module.
hiddenimports = collect_submodules("email_town_labeler")
# The web UI (html/js/css/icons) is package data, read through importlib.resources.
datas = collect_data_files("email_town_labeler")
# Gmail is built from the static discovery document; ship that one, not all 108 MB of them.
datas += collect_data_files("googleapiclient", includes=["discovery_cache/documents/gmail.v1.json"])

a = Analysis(  # noqa: F821
    [str(ROOT / "scripts" / "email_town_app.py")],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=[
        # the `embed` backend (PyTorch) is not part of the app; the ONNX one is
        "torch",
        "transformers",
        "sentence_transformers",
        # onnxruntime's model-conversion tools (and the Pillow they pull in); the app only runs a session
        "onnxruntime.transformers",
        "onnxruntime.quantization",
        "onnxruntime.tools",
        "PIL",
        "hf_xet",  # optional huggingface_hub accelerator; hf: refs download over plain HTTPS without it
        "tkinter",
        "matplotlib",
        "IPython",
        "pytest",
        "PyInstaller",
    ],
    noarchive=False,
)

# hooks-contrib's googleapiclient hook ships every API's discovery document; the app builds Gmail only.
DOCS = "googleapiclient/discovery_cache/documents/"
a.datas = [
    d for d in a.datas if not d[0].replace("\\", "/").startswith(DOCS) or d[0].endswith("/gmail.v1.json")
]

if not (MAC or WINDOWS):
    # WebKitGTK starts helper processes from a path compiled into the system's libwebkit2gtk, so the
    # library must be the system's, and with it the GTK/GLib stack it links against: mixing a bundled
    # GLib with the system's WebKit breaks on any other distribution. Keep only the Python side (gi).
    SYSTEM_GUI = re.compile(
        r"lib(gtk|gdk|glib|gobject|gio|gmodule|gthread|girepository|webkit2gtk|javascriptcoregtk|soup|"
        r"pango|cairo|harfbuzz|atk|atspi|epoxy|fontconfig|freetype|X|xcb|wayland|xkbcommon|gst|secret|dbus)"
    )
    a.binaries = [b for b in a.binaries if not SYSTEM_GUI.match(Path(b[0]).name)]
    a.datas = [
        d
        for d in a.datas
        if not d[0].replace("\\", "/").startswith(
            ("gi_typelibs/", "share/icons/", "share/themes/", "share/locale/", "share/glib-2.0/", "lib/gdk-pixbuf")
        )
    ]

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    debug=False,
    strip=False,
    upx=False,
    console=False,  # a window app: no terminal on Windows; the model worker runs without one too
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,  # the build machine's: arm64 on Apple Silicon
    codesign_identity=None,  # ad hoc here; scripts/release_macos.py signs and notarizes the release copy
    entitlements_file=None,
    icon=str(WEB / ("app-icon.icns" if MAC else "app-icon.png")),  # Windows: PNG -> ICO through Pillow
)

coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=NAME)  # noqa: F821

if MAC:
    short = re.match(r"\d+(\.\d+)*", __version__).group()
    app = BUNDLE(  # noqa: F821
        coll,
        name="Email Town.app",
        icon=str(WEB / "app-icon.icns"),
        bundle_identifier="com.scamai.email-town",
        version=short,
        info_plist={
            "CFBundleName": "Email Town",
            "CFBundleDisplayName": "Email Town",
            "CFBundleShortVersionString": short,
            "CFBundleVersion": __version__,
            "LSMinimumSystemVersion": "14.0",
            "LSApplicationCategoryType": "public.app-category.productivity",
            "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": "MIT License. Scam.ai",
            # the window loads the workbench from http://127.0.0.1:<port>
            "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        },
    )
