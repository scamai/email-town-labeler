"""`python -m email_town_labeler` (used by the Windows service: `pythonw -m email_town_labeler run`)."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
