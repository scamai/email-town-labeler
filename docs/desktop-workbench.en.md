English · [简体中文](desktop-workbench.md)

[Back to README](../README.md)

# On-device unified mail workbench

[Download the app](#download-the-app) · [Open from source](#open-from-source) · [Connect accounts](#connect-accounts) · [Process mail](#process-mail) · [Keyboard shortcuts](#keyboard-shortcuts) · [Appearance](#appearance) · [Privacy](#privacy) · [Recovery and verification](#recovery-and-verification)

The workbench combines explicitly authorized mailboxes into one local list, with Inbox, Sent, Archive and
Trash views, common categories, search and completion state. It supports explicit send/reply/forward,
read/unread, starred, archive, trash/restore and attachment actions. Manual categories and completion remain local.
It runs as the Email Town desktop app (macOS: signed and notarized; Windows/Linux: unsigned; not in an app store) or from source in your browser.

## Download the app

From the first release after v0.1.1 (v0.1.1 has none), each release page attaches a ready-to-run app; it
needs no terminal, Python or uv. On macOS open the disk image and drag Email Town to Applications; on
Windows and Linux unzip it and double-click.

| File | Runs on |
|---|---|
| `Email-Town-<version>-macos-arm64.dmg` | macOS 14 or later on Apple Silicon. Signed by Reality Inc. and notarized by Apple: it opens without a warning. Intel Macs: [open from source](#open-from-source) |
| `Email-Town-<version>-windows-x64.zip` | Windows 10/11 x64 with the Microsoft Edge WebView2 Runtime (included in Windows 11) |
| `Email-Town-<version>-linux-x64.zip` | Built on Ubuntu 24.04 (glibc 2.39 or newer). Needs the distribution's GTK 3 and WebKitGTK 4.1: `sudo apt install gir1.2-webkit2-4.1` on Debian/Ubuntu |

v0.1.2 has no Windows file: its Windows build failed the release checks, and it follows in a later release.

**The Windows and Linux apps are not signed yet**, so the first open asks for one extra step. Only do this for
a zip downloaded from this repository's Releases page:

- **Windows:** SmartScreen shows "Windows protected your PC". Click **More info → Run anyway**.
- **Linux:** run `email-town` in the unzipped `email-town` folder.

The window shows the same workbench as the browser version. Links to the original message in Gmail or
Outlook, the Microsoft sign-in page and Google sign-in open in your default browser; attachment downloads
ask where to save. **Closing the window stops the workbench** and its model process. If
`email-town-labeler desktop` already serves the same data folder, the app opens a window on it instead and
leaves it running when you close the window.

The app uses the same configuration and data folders as the CLI (`EMAIL_TOWN_LABELER_HOME` moves them, as
for the CLI). The model is not inside the app: with no `config.toml` the workbench classifies with the published
on-device model `town-modernbert-int8` and downloads it once, sha256-checked, the first time it classifies
mail (263 MB download, 382 MB on disk); the list says so while it downloads and mail stays readable. A
`config.toml` model, including the keyword-only `rules`, always takes precedence. Its log is
`workbench/desktop.log` in the data folder (macOS `~/Library/Application Support/email-town-labeler/`,
Windows `%LOCALAPPDATA%\email-town-labeler\`, Linux `~/.local/share/email-town-labeler/`); it contains no
mail content. To check a download, run the binary with `--self-check`; it reports what the app can load
without opening a window or touching your data:

```sh
"/Applications/Email Town.app/Contents/MacOS/Email Town" --self-check
```

The downloaded app replaces the source-checkout launcher below; delete `~/Applications/Email Town.app` if
you made one, so that only one "Email Town" remains.

**Build it yourself** (from this checkout, on the platform you build for):

```sh
uv run --extra app --group app-build python scripts/build_app.py --zip
uv run --extra app --group app-build python scripts/smoke_app.py -- "dist/Email Town.app/Contents/MacOS/Email Town"
```

`build_app.py` writes `dist/Email Town.app` (macOS), `dist/Email Town/` (Windows) or `dist/email-town/`
(Linux), and with `--zip` the release archive. On an Apple M4 Pro (macOS, Python 3.12) it reported the
build at 11 s, the app at 136 MB and the zip at 49 MB. `smoke_app.py` runs the self-check, opens the app on a new temporary data folder,
checks the server, a second start and a clean quit, and never touches your mail data. Linux also needs
`pywebview[gtk]` (PyGObject) and the GTK development packages; the
[Desktop app workflow](../.github/workflows/app.yml) lists them. That workflow builds and smoke-tests all
three platforms; it runs only when started by hand or by a `v*` tag.

## Open from source

```sh
email-town-labeler desktop --background
```

For a fresh install use `uv tool install 'email-town-labeler[onnx,desktop]'`; to use this source checkout,
use `uv tool install --editable '.[onnx,desktop]'`. The desktop extra provides OS credential-vault support
for IMAP. Classification uses the model in `config.toml`, or `town-modernbert-int8` when there is none (downloaded
once, 263 MB); mail content is not sent to a remote classifier. The desktop workbench is separate from
the label-only CLI service.

Repeated launches reopen the same process. Closing the browser does not stop it.
A macOS source checkout can create a local double-click launcher:

```sh
python3 scripts/install_macos_workbench.py
email-town-labeler desktop --stop
# Foreground diagnostics:
email-town-labeler desktop --no-browser
```

The launcher is `~/Applications/Email Town.app`; it references the installed CLI and adds no login item.
Restart the process after backend source changes. The server binds only a random `127.0.0.1` port.
The bare URL printed in the terminal has no authority; reopen through the launcher. Keep launch links private.

To get the native window from source instead of a browser, install the `app` extra and run `email-town`
(`uv tool install --editable '.[app]'`; on Linux also `pywebview[gtk]` or your distribution's PyGObject).

## Connect accounts

An existing default Gmail authorization is enrolled automatically and listed by its mailbox address. Use **Accounts → + Add** in the sidebar for other
mailboxes. The display name is optional: left empty, the account is listed by its address. The app does not extract Outlook or Apple Mail credentials.

| Provider | Connection | What you need |
|---|---|---|
| Google / Gmail | Browser sign-in, Gmail modify (read/send/manage) | A Google sign-in client: one shipped with your copy, or your own Desktop OAuth client file imported in the dialog ([setup guide](google-cloud-setup.md)). Without either, add Gmail as Other email with an app password. |
| Microsoft / Outlook | Device code sign-in | Nothing when your copy ships a Microsoft app ID; otherwise your own public-client application ID (under **Use your own Microsoft app registration**). Tenant approval may be required. |
| Other email (iCloud, Yahoo, QQ…) | Verified TLS IMAP/SMTP with an app password | Type the address: known providers fill the server settings and link to where the app password is created. Others need the server names. |
| Existing Email Town folder | Explicit absolute `--home` directory | Its own valid Gmail token |

Suggested settings exist for Gmail, iCloud, Yahoo, AOL, Fastmail, Zoho and QQ Mail; they stay editable under
**Server settings**. Outlook.com, Hotmail and Live addresses are routed to Microsoft sign-in, because Outlook.com
no longer accepts app passwords. NetEase (163/126) needs an IMAP ID handshake that is not implemented yet.

Microsoft new accounts require delegated `Mail.ReadWrite`, `Mail.Send`, `User.Read`, and `offline_access`;
existing read-only grants require reauthorization and are never silently expanded. See the [official device
authorization protocol](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-device-code).
Gmail grants cannot authorize Microsoft mailboxes. On-premises Exchange is not automatically a Graph
mailbox; it needs administrator-supported IMAP or a different adapter.

The account's **⋯** menu pauses/resumes synchronization or reconnects it. Pause retains loaded mail and
preview access. Reconnection preserves workflow state and checks mailbox identity; use add account for
a different mailbox. Each additional mailbox needs its own user authorization.

## Process mail

Filter across all or individual accounts by today, old unfinished mail, category, completion, or subject/sender
search. Open a plaintext preview, change its local category, mark complete, or batch-edit and undo.
Completion survives restart; undo tokens live only in the current process. Selection is limited to loaded,
currently filtered results and 500 messages per action across accounts. The initial window is 100 inbox
messages per account; Load more adds 100 up to 1,000 per account.

Send/reply/reply-all/forward support To/Cc/Bcc, plaintext bodies and explicit attachment selection. Drafts
and attachments are stored privately under `workbench/mail`; failed, unknown or partial sends retain the
draft and are never retried automatically. Gmail/SMTP sends are limited to 20 MiB total and Graph to 2 MiB;
downloads are limited to 20 MiB for Gmail/IMAP and 8 MiB for Graph. Forwarding includes body only; attachments
must be re-added explicitly.

Existing Gmail project labels supply categories directly. Other unclassified mail uses one shared local model; your own Sent mail is not classified.
Local overrides take precedence and are independent of completion. They currently neither write back to the
provider nor train the existing CLI feedback model. Images referenced by the HTML body appear below the
plaintext body; the sender's HTML layout, scripts and styles are never rendered. CID-linked PNG, JPEG,
GIF, WebP, AVIF and BMP images load from the mailbox automatically (8 MiB each, 32 MiB per open preview).
HTTPS external images stay blocked until **Load external images** is clicked for that preview. This can
tell the sender the message was opened. Unsupported sources, SVGs and oversized inline files can be viewed
through the original message or downloaded attachments. Gmail and Outlook links open the original message.

Inbox, Sent, Archive and Trash are separate views. Counts cover loaded messages only; truncation is
explicit. At most 32 accounts can be connected. Archive moves mail out of Inbox while preserving local
workflow records. Trash moves selected originals to the provider trash and Restore returns them to Inbox.
These changes synchronize to other clients using the same mailbox. Local category/completion changes
stay on this device. Selecting all results selects only loaded, filtered messages, not the whole mailbox.
Synchronization runs every 60 seconds. The UI holds one request open (up to 10 seconds) and updates as soon as
local state changes. A cold Gmail list shows rows in batches of 25 while the rest load; returning to a folder shows
its last list at once and then revalidates it. The newest 10 bodies per Microsoft/IMAP account are read ahead so they open instantly. Failure in one account retains its last
list with an error and leaves others usable. Completion survives restart; undo tokens live only in the current process.

The top bar holds **Compose** and **Drafts**. The list shows sender/time, subject, then category/account.
Select messages to reveal batch actions. **More** groups read/star controls and local completion/category
actions separately. The preview keeps archive/trash at the top and reply/forward below the sender details.
Below 1,200 CSS pixels, opening a message replaces the list; **Back** returns to the same message.
Below 768 pixels, the menu button opens filters/accounts. Compose keeps **Send** and **Save draft** visible
while its fields scroll. See the [layout decisions](design-spec.md#desktop-layout-and-alignment).

## Keyboard shortcuts

Press <kbd>?</kbd> in the app (or **Keyboard shortcuts** at the bottom right) for the list. Single keys follow
Gmail and work when you are not typing in a field or composing with an input method; ⌘ combinations follow
Apple Mail (Ctrl on Windows and Linux).

| Keys | Action |
|---|---|
| <kbd>j</kbd> / <kbd>k</kbd> (or <kbd>↓</kbd> / <kbd>↑</kbd>) | Next (older) / previous (newer) message |
| <kbd>/</kbd> or <kbd>⌘F</kbd> | Search |
| <kbd>g</kbd> then <kbd>i</kbd> / <kbd>s</kbd> / <kbd>a</kbd> / <kbd>t</kbd> | Inbox, Sent, Archive, Trash |
| <kbd>x</kbd> | Select or deselect the open message |
| <kbd>e</kbd> | Archive (the selection if there is one, else the open message) |
| <kbd>#</kbd> or <kbd>Delete</kbd> | Move to Trash (asks first, as the button does) |
| <kbd>s</kbd> · <kbd>Shift U</kbd> · <kbd>Shift I</kbd> · <kbd>d</kbd> | Star or unstar · mark unread · mark read · done or reopen |
| <kbd>c</kbd> or <kbd>⌘N</kbd> | New message |
| <kbd>r</kbd> / <kbd>a</kbd> / <kbd>f</kbd> or <kbd>⌘R</kbd> / <kbd>⌘⇧R</kbd> / <kbd>⌘⇧F</kbd> | Reply / reply all / forward |
| <kbd>⌘Enter</kbd> | Send the message you are writing |
| <kbd>Esc</kbd> | Back to the list; close a menu or dialog |

In the macOS app the menus also give <kbd>⌘Z</kbd> / <kbd>⇧⌘Z</kbd> (undo, redo), <kbd>⌘X</kbd> <kbd>⌘C</kbd>
<kbd>⌘V</kbd> <kbd>⌘A</kbd>, <kbd>⌘M</kbd> (minimize), <kbd>⌘H</kbd> (hide) and <kbd>⌘Q</kbd> (quit). The window has
no right-click menu; use the keyboard to copy and paste.

## Appearance

The interface, account setup, status messages and errors default to English. Mail content,
account names chosen by the user, category IDs and existing provider labels are not translated.
Dates use an English format and the device's local time zone.

Category colors are consistent in the sidebar, message list and preview, including after a
manual change or undo. Their hue families follow the existing Gmail taxonomy; the desktop shades
adapt to light/dark mode for readable text. This does not repaint provider labels.

| Category | Hue |
|---|---|
| Action needed | Orange |
| New sender | Blue |
| Newsletters | Green |
| Orders & bills | Purple |
| Promotions | Rose |
| Travel | Teal-blue |
| Updates | Gold |
| Unclassified | Slate |

Labels remain visible, and selected categories have a checkmark plus an inset edge. This follows
[W3C's guidance to supplement color with text or shape](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html).
The browser verification script measures all eight selected-filter palettes in both themes and
checks matching message-badge colors and preview updates. The minimum category text contrast is
required to remain at least 4.5:1. These checks are not a complete accessibility certification.

The envelope-and-house logo is authored in `desktop/web/logo.svg`; the same mark supplies the
favicon and the macOS icon. Rebuild the PNG/ICNS resources on macOS, then refresh the launcher:

```sh
uv run --with playwright python scripts/build_desktop_brand.py
python3 scripts/install_macos_workbench.py
```

The binary assets are included in the package; ordinary users do not need image tooling to open
the app. The same `app-icon.icns` is the [desktop app](#download-the-app)'s icon; neither is signed.

## Privacy

The UI explicitly extends the old CLI's IDs/counts-only output rule: subjects, senders and bodies appear in
the **local browser's memory**. The process retains at most 128 previews (including the newest 10 per Microsoft/IMAP account read ahead), each with at most 256 KiB of body
text plus up to 50 image references. Image blobs are released when switching messages or losing the session.
External images load directly in the browser without a referrer or the workbench bearer token; the remote
host sees the browser's IP and image URL, and its own cookie/cache policies apply. Bodies are never
written to disk. The list is: `cache.sqlite` keeps subject, sender, date, read/star state, category and IDs of
each folder's loaded window (plus Gmail header metadata and history cursor) so a restart shows mail at once and
Gmail re-reads only what changed. Delete it to clear it. There is no telemetry or remote classification. Drafts saved explicitly or before
sending persist locally, including attachment bytes, without additional application-level encryption. OS swap, browser extensions
and screenshots are part of the local machine's privacy boundary.

The workbench data directory stores registry connection settings (including IMAP usernames), hashed mailbox
identities, account-scoped hashed message IDs, manual categories, completion and revisions, and the list cache. Explicitly saved drafts contain recipients, body and attachment content in the private local draft store; operation receipts may include rejected SMTP recipient addresses for recovery. OAuth credentials
and the private launch URL are private files; IMAP passwords use the OS vault. Logs exclude mail content.
Read the full [privacy model](privacy.md).

Provider network access is still needed to fetch mail. On-device means local classification/organization,
not offline downloading. The existing Gmail labeling service runs independently. Contacts from one account
are not used to classify another account.

## Recovery and verification

Expired authorization: **⋯ → Reauthorize this mailbox**. Network failure: restore connectivity and refresh/wait.
Unavailable OS vault: IMAP enrollment stops without plaintext fallback. Model failure: preview and manual
classification remain available.

```sh
uv run pytest tests/test_desktop_*.py
uv run --with playwright playwright install chromium
uv run --with playwright python scripts/check_desktop_ui.py
uv run --with playwright python scripts/check_mail_ui.py
uv run --with playwright python scripts/check_layout_ui.py
```

Browser tests use clearly labeled synthetic mail from two accounts and exercise actual loopback HTTP actions,
filters, plaintext preview, completion/undo, category persistence, keyboard use, and four viewport widths in
light/dark themes. They do not establish live OAuth/provider behavior for unauthorized accounts. Real cross-provider
acceptance still needs separately authorized accounts, new-mail/expiry/reconnection checks and client feedback tests.
