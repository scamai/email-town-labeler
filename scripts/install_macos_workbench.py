"""Create a local macOS launcher for the installed CLI (not a signed distributable app).

Run: python3 scripts/install_macos_workbench.py
The launcher starts/reopens the workbench, and never installs a login item.
"""

import argparse
import plistlib
import shlex
import shutil
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", default=shutil.which("email-town-labeler"))
    parser.add_argument("--destination", type=Path, default=Path.home() / "Applications" / "Email Town.app")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error(
            "this launcher requires macOS; use email-town-labeler desktop --background on other systems"
        )
    if not args.cli or not Path(args.cli).is_file():
        parser.error("install email-town-labeler first, or specify --cli /absolute/path")
    app = args.destination.expanduser().absolute()
    info = app / "Contents" / "Info.plist"
    identity = "com.scamai.email-town.local"
    if app.exists() and (
        not info.is_file() or plistlib.loads(info.read_bytes()).get("CFBundleIdentifier") != identity
    ):
        parser.error("destination already exists and is not this launcher; choose a new path")
    executable = app / "Contents" / "MacOS" / "email-town"
    executable.parent.mkdir(parents=True, exist_ok=True)
    # Do not resolve the executable symlink: an installed CLI may depend on its managed environment.
    executable.write_text(
        "#!/bin/sh\nexec " + shlex.quote(str(Path(args.cli).absolute())) + " desktop --background\n"
    )
    executable.chmod(0o755)
    info.write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": identity,
                "CFBundleName": "Email Town",
                "CFBundleDisplayName": "Email Town",
                "CFBundleExecutable": "email-town",
                "CFBundlePackageType": "APPL",
                "CFBundleVersion": "1",
                "LSUIElement": True,
            }
        )
    )
    print(f"Local launcher created: {app}")


if __name__ == "__main__":
    main()
