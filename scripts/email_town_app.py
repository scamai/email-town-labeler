"""Entry script that PyInstaller freezes into the Email Town app (scripts/email-town.spec).

Everything lives in the package: desktop/window.py main() opens the window, or, given
--email-town-model-worker / --self-check, runs the model worker / the bundle self-check instead.
"""

import sys

from email_town_labeler.desktop.window import main

if __name__ == "__main__":
    sys.exit(main())
