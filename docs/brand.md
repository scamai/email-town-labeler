English

[Back to README](../README.md)

# Email Town identity

## Contents

- [The mark](#the-mark)
- [Colours](#colours)
- [Where each file is used](#where-each-file-is-used)
- [Reproduce the icon](#reproduce-the-icon)

## The mark

A letter closed with a wax seal; the seal is pressed with a little gable house, the "town". The sealed
letter is the silhouette and has to read at Dock size (27 pt, 54 device pixels); the house adds character at
larger sizes. A seal says *private* and *genuine*: mail is sorted on this device, and a letter is something
you can trust (信 is both the letter and trust).

Two rules come from testing the mark in a real Dock. The flap's V must stay visible at 16 px: without it, a
pale card with a dot in the middle reads as a flag. And the drawing is made for Apple's Icon Composer, so
macOS 26 and later give it the system's glass and its dark and tinted looks, like the icons around it.

## Colours

| Part | Light | Dark |
|---|---|---|
| Tile | `#7b67d8` → `#5f4dc0` | `#2b2253` → `#16112f` |
| Paper | `#f7f5ff` → `#e5dfff`, flap edge `#b3a6ec` | same |
| Wax | `#a08ff0` → `#7462d2`, house `#5643b0` | same |

The owner chose the purple on 2026-11-21, from the system's tinted rendering of a teal-and-kraft study.
The values live in one place, the top of `scripts/build_desktop_brand.py`.

## Where each file is used

| File | Used by |
|---|---|
| `brand/AppIcon.icon` | The Icon Composer document. Open it in Icon Composer to preview every look |
| `brand/Assets.car` | macOS 26+: compiled from the document, bundled as `CFBundleIconName` `AppIcon` |
| `src/email_town_labeler/desktop/web/app-icon.icns` | Older macOS and Finder (`CFBundleIconFile`): 16 to 1024 px with Apple's margin |
| `src/email_town_labeler/desktop/web/app-icon.png` | The Windows `.ico` and the Linux window icon |
| `src/email_town_labeler/desktop/web/logo.svg` | The workbench header and the browser tab |

## Reproduce the icon

The drawing is code: [`scripts/build_desktop_brand.py`](../scripts/build_desktop_brand.py) holds the
geometry and the colours. On macOS with Xcode 26 or later (Icon Composer's `ictool`, `actool`, `iconutil`):

```bash
uv run --with pillow python scripts/build_desktop_brand.py
```

It rewrites every file in the table and prints the folder of the small renders; look at the 16 and 32 px
images before committing. `--sources-only` rewrites just the Icon Composer document and `logo.svg`, on any
OS. `tests/test_desktop_brand.py` fails when the committed sources differ from what the script draws, and
checks the sizes and wiring of the rendered files, which CI cannot rebuild without Xcode.
