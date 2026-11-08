English

[Back to README](../README.md)

# Releasing the desktop app (maintainers)

[macOS](#macos-signed-and-notarized) · [What the script checks](#what-the-script-checks) · [Windows and Linux](#windows-and-linux)

## macOS: signed and notarized

A Mac with Xcode's command-line tools, the company's **Developer ID Application** certificate in the login
keychain, and a notarytool keychain profile for the same team. The pipeline follows
[scamai/earlet](https://github.com/scamai/earlet) (`tools/release_macos.py`), which ships a bundled
Python interpreter the same way.

```bash
python scripts/build_app.py                       # dist/Email Town.app (unsigned)
export EMAIL_TOWN_SIGN_IDENTITY="Developer ID Application: Reality Inc. (BB4TFKG82B)"
export EMAIL_TOWN_NOTARY_PROFILE=AC_NOTARY        # created once with: xcrun notarytool store-credentials
python scripts/release_macos.py --output dist/release
```

The result is `dist/release/Email-Town-<version>-macos-<arch>.dmg` and `release-receipt.json`. Add
`--no-notarize` to sign and image a build without submitting it to Apple; its receipt says it is not notarized.

Sign with the company identity. A Mac that also holds a personal Developer ID certificate for the same team
lists both; the script then refuses to choose and asks for `EMAIL_TOWN_SIGN_IDENTITY`, so a release is never
signed with a person's name by accident.

Publishing is a separate, deliberate step. A `v*` tag makes `.github/workflows/app.yml` attach the Windows and
Linux zips; attach the notarized image to the same release yourself:

```bash
gh release upload v<version> dist/release/Email-Town-<version>-macos-arm64.dmg
```

## What the script checks

- Refuses an ad-hoc or Apple Development identity, a missing notary profile and an existing output folder.
- Clones the app; the input is never changed. Signs every Mach-O file deepest first with the hardened runtime
  and a secure timestamp, then nested bundles, then the app. It never uses `--deep`. Only executables get
  `scripts/macos-app.entitlements` (the bundled interpreter needs JIT/unsigned executable memory for libffi
  callbacks and loads extension modules).
- Fails unless every Mach-O file carries the hardened runtime and a timestamp.
- Notarizes and staples the app, builds the image with an Applications link, signs, notarizes and staples the
  image, then runs `spctl` on both. Apple's notary log is kept beside the receipt, also for a rejection.
- The receipt records the identity, what was signed, Apple's submission ids and answers, and the image's size
  and SHA-256. It is not a clean-machine test: open the image on another Mac before announcing a release.

## Windows and Linux

`.github/workflows/app.yml` builds unsigned Windows and Linux folders. Windows SmartScreen warns about an
unsigned app until it is signed with a code-signing certificate; that certificate is not set up yet.
