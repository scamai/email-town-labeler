English · [简体中文](desktop-workbench.md)

[Back to README](../README.md)

# On-device unified mail workbench

[Download the app](#download-the-app) · [Open from source](#open-from-source) · [Connect accounts](#connect-accounts) · [Process mail](#process-mail) · [Keyboard shortcuts](#keyboard-shortcuts) · [Appearance](#appearance) · [Privacy](#privacy) · [Recovery and verification](#recovery-and-verification)

The workbench combines explicitly authorized mailboxes into one local list, with Inbox, Sent, Archive and
Trash views, common categories and search. It supports explicit send/reply/forward,
read/unread, starred, archive, trash/restore and attachment actions. Manual categories remain local.
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

v0.1.2 has no Windows file (its Windows build failed the release checks); v0.1.3 and later carry one.

**The Windows and Linux apps are not signed yet**, so the first open asks for one extra step. Only do this for
a zip downloaded from this repository's Releases page:

- **Windows:** SmartScreen shows "Windows protected your PC". Click **More info → Run anyway**.
- **Linux:** run `email-town` in the unzipped `email-town` folder.

The window shows the same workbench as the browser version. Links to the original message in Gmail or
Outlook, the Microsoft sign-in page and Google sign-in open in your default browser; attachment downloads
ask where to save. **On macOS, closing the window keeps Email Town running in the background**: it keeps
syncing, sorting and finishing the mail actions you started, and its Dock icon brings the window back.
**Quitting** (<kbd>⌘Q</kbd>, or Quit in the Dock) first finishes the mail actions already started, so a Trash
of hundreds of messages runs to the end, then stops the workbench and its model process; it gives up only when
nothing has finished for 90 seconds (offline). On Windows and Linux, closing the window quits the same way. If
`email-town-labeler desktop` already serves the same data folder, the app opens a window on it instead and
leaves it running when you close the window.

The app uses the same configuration and data folders as the CLI (`EMAIL_TOWN_LABELER_HOME` moves them, as
for the CLI). The model is not inside the app: with no `config.toml` the workbench classifies with the published
on-device model `town-modernbert-int8` and downloads it once, sha256-checked, the first time it classifies
mail (263 MB download, 382 MB on disk); the list says so while it downloads and mail stays readable. A
`config.toml` model, including the keyword-only `rules`, always takes precedence. Its log is
`workbench/desktop.log` in the data folder (macOS `~/Library/Application Support/email-town-labeler/`,
Windows `%LOCALAPPDATA%\email-town-labeler\`, Linux `~/.local/share/email-town-labeler/`); it contains no
mail content. To make "what happened to this message" answerable it records, by the first 8 characters of
each message's opaque key: every open and how (click, `j`/`k`, Shift+J, auto-advance, arrows), selection
changes, handled shortcut keys, mailbox actions and their per-message results, each open-to-read decision,
and read/star changes the mailbox reported on sync. The page's events pass through a filter that keeps
only short tokens, so subjects, addresses and text cannot be written. Other libraries are logged only from WARNING, and then by logger name, level and
exception type without their text, because such a line can carry a request URL or a credential. To check a download, run the binary with `--self-check`; it reports what the app can load
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
for IMAP. Classification uses the model in `config.toml`, or `town-modernbert-int8` when it names none (downloaded
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

On first run, with no mailbox yet, **Add an email account** in the middle of the list is the way in. An existing default Gmail authorization is enrolled automatically and listed by its mailbox address. Use **Accounts → + Add** in the sidebar for other
mailboxes. The display name is optional: left empty, the account is listed by its address. The app does not extract Outlook or Apple Mail credentials.

| Provider | Connection | What you need |
|---|---|---|
| Google / Gmail | Browser sign-in, Gmail modify (read/send/manage) | A Google sign-in client: one shipped with your copy, or your own Desktop OAuth client file imported in the dialog ([setup guide](google-cloud-setup.md)). Without either, add Gmail as Other email with an app password. |
| Microsoft / Outlook | Device code sign-in | Nothing when your copy ships a Microsoft app ID; otherwise your own public-client application ID (under **Use your own Microsoft app registration**). Tenant approval may be required. |
| Other email (iCloud, Yahoo, QQ…) | Verified TLS IMAP/SMTP with an app password | Type the address: known providers fill the server settings and link to where the app password is created. Others need the server names. |
| Existing Email Town folder | Explicit absolute `--home` directory | Its own valid Gmail token |

### What a downloaded copy can connect today

The app on the Releases page (through v0.1.6) **ships no Google sign-in client and no Microsoft app ID**, so the
**Google / Gmail** and **Microsoft / Outlook** buttons are not usable for most people yet. App passwords work now:

| Mailbox | Works in the downloaded app? | How |
|---|---|---|
| Gmail (personal) | Yes, with an app password | Turn on 2-Step Verification, create a 16-character app password at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords), then in Email Town choose **Accounts → +** → **Other email** and enter the address and that password. Gmail labels are not kept this way |
| Gmail (Google Workspace) | Depends on the administrator | As above; if the administrator turned off IMAP or app passwords, it cannot connect |
| iCloud, Yahoo, AOL, Fastmail, Zoho, QQ Mail | Yes | **Other email** → type the address; the server settings are filled in and the dialog links to where the app password (QQ: authorization code) is created |
| Outlook.com / Hotmail / Microsoft 365 | Not yet | Needs your own public-client registration in Microsoft Entra, entered under **Use your own Microsoft app registration**; a later version will ship one |
| NetEase 163 / 126 | No | Needs an IMAP ID handshake that is not implemented |

Browser sign-in with **Google / Gmail** (no app password, no 2-Step Verification needed) arrives in releases once
Google has verified the `gmail.modify` scope. Builds made with `scripts/build_app.py --google-client` offer it
already: since 2026-11-21 the project's sign-in client is published ("In production") but not verified, so Google
shows **"Google hasn't verified this app"** during sign-in (**Advanced → Go to Email Town (unsafe)**), at most 100
Google accounts can ever sign in through it before verification, and sign-ins no longer expire every 7 days
([why](google-cloud-setup.md#4-audience-testing-vs-production-external-only)). Non-Google mailboxes do not depend
on Google's review at all.

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

Three panes, as in Outlook: folders and filters, the message list, the reading pane. Drag either pane
border (or focus it and press ←/→) to resize; double-click restores the default. Widths are kept on this device
and come back after you quit and reopen the app.

The folder pane lists Inbox, Drafts, Sent, Archive and Trash. Being done with a message is **Archive**
(<kbd>e</kbd> or <kbd>d</kbd>), as in Gmail: it leaves the Inbox here and in the mailbox itself, so every
mail app agrees. It is in **Archive**, category intact, and **Restore** puts it back in the Inbox. (Before
0.1.7 a separate local **Done** only hid mail on this device; mail marked that way shows in the Inbox again
until it is archived.)

**Bundles.** In the Inbox, Newsletters, Promotions, Updates, Orders & bills and Travel each collapse into one
row, placed where its newest message would be, with how many messages and how many unread, and the first
senders. Action needed, New sender and People always stay as their own rows. Click a bundle to open its
messages; **Archive all** archives the whole bundle, with the same 5-second Undo. Inside an opened bundle,
**Show one by one** lists that category's messages individually in the Inbox again (**Bundle them** undoes
it); the choice is kept on this device. A search, a chosen category or another folder always lists messages
one by one. Keyboard navigation (<kbd>j</kbd>/<kbd>k</kbd>) moves between messages and skips bundle rows.

Below the folders, categories and accounts each show one number: their unread mail in the current scope.
Choose a category to filter by it and again (or its chip in the list header) to clear it. With one
account, there is no "All accounts" entry and rows carry no account name. The list header holds the date
range (All / Today / Earlier), an **Unread** toggle, a chip per active filter, and the message count; search sits in the top
bar and matches only the subjects and senders of loaded mail. Filters compose; **Clear search and filters** returns to the default view. **Unread** lists only the
unread mail in the current scope (choose Updates, then Unread); a message read in that view stays listed,
no longer bold, until the folder, category, account, date range or search changes, as in Gmail and
Outlook, so it does not vanish while you read it. Each row is two fixed
lines: the sender's name and the time; the subject, its category and the star. Unread rows are bold with
a blue bar at the left edge. Totals and classification sources (sorted automatically,
corrected by you, awaiting classification) are in each entry's tooltip and accessible name. Only the
active folder has a loaded unread count; other folders are not reported as zero.
They do not measure accuracy or establish that unreviewed classifications are correct. Failed or paused
accounts are distinguished from genuinely empty folders.
Manual categories survive restart; undo tokens live only in the current process. Selection is limited to loaded,
currently filtered results and 500 messages per action across accounts. The initial window is 100 inbox
messages per account; Load more adds 100 up to 1,000 per account.

Send/reply/reply-all/forward support To/Cc/Bcc, plaintext bodies and explicit attachment selection. Drafts
and attachments are stored privately under `workbench/mail`; failed, unknown or partial sends retain the
draft and are never retried automatically. Gmail/SMTP sends are limited to 20 MiB total and Graph to 2 MiB;
downloads are limited to 20 MiB for Gmail/IMAP and 8 MiB for Graph. Forwarding includes body only; attachments
must be re-added explicitly. Send closes the editor and waits 5 seconds before the message leaves: **Undo**
in the toast keeps it unsent and reopens it in the editor; quitting inside the window sends it at once.
An IMAP account sends through the SMTP server you enter, over TLS (STARTTLS or implicit TLS) only. On an IMAP
server without UID MOVE, Archive, Trash and Restore are refused and nothing is deleted.

Existing Gmail project labels supply categories directly. Other mail without one uses one shared local model; your own Sent mail is not classified.
**People** is the model's "none of these": mail written to you by a person (a colleague's reply, a forwarded thread)
rather than sent by a service, so it needs no category. It also holds mail the model was not sure enough about: a
class below its confidence bar (`[model] min_confidence`, 0.55 by default; `[policy] action_threshold` for Action
needed) counts as none of these. Mail still waiting for the model is listed there too until
it is classified (the reader says **Sorting…**); the category's tooltip counts it as awaiting classification.
Your own Sent mail reads **Not classified** and is not counted under People.
Local overrides take precedence and are not written back to the provider.

**The model learns from the categories you choose.** Each model decision keeps the model's scores over the
seven classes and eight yes/no facts (mailing-list headers, Gmail tab, calendar invite) in `learning.sqlite`:
numbers under the message's opaque key, no mail content. A category you set on a message is a correction of
that decision (Undo takes it back); a model category left alone for 7 days counts as a weak confirmation.
Sender rules are not used, so one rule does not turn a single sender into dozens of examples. Once there are
30 corrections, at most once a day, the workbench fits a small personal head on top of the model with the same
code as [`learn`](models.md#improving-from-use-learn) and keeps it only if it beats the unchanged model on
held-out messages; it also re-tunes the action and promotional thresholds. The listed model categories are
re-decided from the stored scores at once (the model does not run again), and new mail uses the head as it
arrives. Mail listed before learning existed is classified once more so it can be learned from too.
`[learn] auto = false` in `config.toml` turns it off; deleting `personal.json` in the workbench folder
goes back to the model as shipped.

**Decide once per sender.** The category dialog offers **Always use this category for mail from …**. With it,
that sender's other loaded mail and everything they send later get the category you chose, ahead of the
provider label and the model (a category you set on one message still wins for that message). This is how
you screen a new sender: a newsletter that landed in Action needed moves into the Newsletters bundle along
with its future issues. **Undo** in the toast removes the rule again; reopening the dialog shows the box
checked, and unchecking it removes the rule. Rules apply to received mail only and stay on this device.

A message with an HTML version opens formatted, as Gmail shows it: the sender's layout, colours, buttons and
inline images. The HTML is reduced to an allowlist first (no scripts, forms, frames, SVG or event handlers;
links keep only http, https and mailto) and shown in a sandboxed frame that cannot run code, submit forms
or reach the network except for images; links open in the system browser. **Plain text** switches that
message to its text version, where images appear below the body. CID-linked PNG, JPEG, GIF, WebP, AVIF and
BMP images load from the mailbox automatically (8 MiB each, 32 MiB per open preview in plain text).
HTTPS external images stay hidden until **Show images** (formatted) or **Load external images** (plain
text) is clicked for that message. This can tell the sender the message was opened. Unsupported sources, SVGs and oversized inline files can be viewed
through the original message or downloaded attachments. Gmail and Outlook links open the original message.

Staying on a message for 1.5 seconds marks it read in its mailbox, as Gmail's reading pane does (passing
over it with j/k does not): the row loses its bold and blue dot at once, and the
write follows in the background (a failed write puts the row back). **Mark unread** keeps it unread until you open it
again. Accounts without mail-management permission, or paused, are left unchanged. The open folder's tab shows its
unread count for the current filters.

As in Gmail, hovering a row (or reaching it with the keyboard) shows Archive, Trash and Mark read/unread in place of
the time, and every row has a star; they act without opening the message. Archiving or trashing the open message
opens the next, older one in a wide window and returns to the list in a narrow one. Touch screens show the star
only; read-only accounts show neither.

Inbox, Sent, Archive and Trash are separate views. Counts cover loaded messages only; truncation is
explicit. At most 32 accounts can be connected. Archive moves mail out of Inbox while preserving local
workflow records. Trash moves selected originals to the provider trash and Restore returns them to Inbox. For 5 seconds after
Archive or Trash the toast offers **Undo**: the change is held back from the mailbox until then, so Undo sends
nothing; quitting inside the window sends it at once.
These changes synchronize to other clients using the same mailbox. Local category changes
stay on this device. Selecting all results selects only loaded, filtered messages, not the whole mailbox.
Synchronization runs every 60 seconds. The UI holds one request open (up to 10 seconds) and updates as soon as
local state changes. A cold Gmail list shows rows in batches of 25 while the rest load; returning to a folder shows
its last list at once and then revalidates it. The newest 10 bodies per Microsoft/IMAP account are read ahead so they open instantly. Failure in one account retains its last
list with an error and leaves others usable. Manual categories survive restart; undo tokens live only in the current process.

The top bar holds **Compose** and **Drafts**. The list shows sender/time, subject, then category/account.
Select messages to reveal batch actions. **More** groups read/star controls and the local category action
separately. The preview keeps archive/trash at the top and reply/forward below the sender details.
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
| <kbd>⌘A</kbd> | Select the open message's text, not the folders, list or bundles (in a field it selects the field) |
| <kbd>g</kbd> then <kbd>i</kbd> / <kbd>s</kbd> / <kbd>a</kbd> / <kbd>t</kbd> | Inbox, Sent, Archive, Trash |
| <kbd>x</kbd> | Select or deselect the open message |
| <kbd>Shift</kbd>-click · <kbd>⌘</kbd>-click | Select a range (rows or checkboxes) · add or remove one message without opening it |
| <kbd>Shift j</kbd> / <kbd>Shift k</kbd> | Extend the selection down / up |
| <kbd>*</kbd> then <kbd>a</kbd> / <kbd>n</kbd> / <kbd>r</kbd> / <kbd>u</kbd> / <kbd>s</kbd> / <kbd>t</kbd> | Select all, none, read, unread, starred, unstarred (also in the ▾ menu beside "Select all loaded") |
| <kbd>e</kbd> or <kbd>d</kbd> | Archive: done with it (the selection if there is one, else the open message) |
| <kbd>#</kbd>, <kbd>Delete</kbd> or <kbd>Backspace</kbd> | Move to Trash (asks first, as the button does) |
| <kbd>s</kbd> · <kbd>Shift U</kbd> · <kbd>Shift I</kbd> | Star or unstar · mark unread · mark read |
| <kbd>c</kbd> or <kbd>⌘N</kbd> | New message |
| <kbd>r</kbd> / <kbd>a</kbd> / <kbd>f</kbd> or <kbd>⌘R</kbd> / <kbd>⌘⇧R</kbd> / <kbd>⌘⇧F</kbd> | Reply / reply all / forward |
| <kbd>⌘Enter</kbd> | Send the message you are writing |
| <kbd>Esc</kbd> | Clear the selection; back to the list; close a menu or dialog |

In the macOS app the menus also give <kbd>⌘Z</kbd> / <kbd>⇧⌘Z</kbd> (undo, redo), <kbd>⌘X</kbd> <kbd>⌘C</kbd>
<kbd>⌘V</kbd>, <kbd>⌘M</kbd> (minimize), <kbd>⌘W</kbd> (close; keeps running), <kbd>⌘H</kbd> (hide) and
<kbd>⌘Q</kbd> (quit). The window has
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
| People | Slate |

Labels remain visible, and selected categories have a checkmark plus an inset edge. This follows
[W3C's guidance to supplement color with text or shape](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html).
The browser verification script measures all eight selected-filter palettes in both themes and
checks matching message-badge colors and preview updates. The minimum category text contrast is
required to remain at least 4.5:1. These checks are not a complete accessibility certification.

The sealed-letter mark (a wax seal pressed with a little house) is drawn by
`scripts/build_desktop_brand.py`, which writes the header mark `desktop/web/logo.svg`, the favicon and
every app icon, including the macOS 26+ Icon Composer icon ([Brand](brand.md)). Rebuild them on macOS with
Xcode 26 or later, then refresh the launcher:

```sh
uv run --with pillow python scripts/build_desktop_brand.py
python3 scripts/install_macos_workbench.py
```

The binary assets are included in the package; ordinary users do not need image tooling to open
the app. The same `app-icon.icns` is the [desktop app](#download-the-app)'s icon. The source-checkout launcher is not
signed; the released macOS app is signed and notarized.

## Privacy

The UI explicitly extends the old CLI's IDs/counts-only output rule: subjects, senders and bodies appear in
the **local browser's memory**. The process retains at most 300 previews (including the newest 30 Gmail or
10 Microsoft/IMAP bodies per account read ahead); each open page caches at most 150 opened or prefetched
message details. Each server preview has at most 262,144 body-text characters
plus up to 50 image references. Image blobs are released when switching messages or losing the session.
External images load directly in the browser without a referrer or the workbench bearer token; the remote
host sees the browser's IP and image URL, and its own cookie/cache policies apply. Bodies are never
written to disk. The list is: `cache.sqlite` keeps subject, sender, date, read/star state, category (with the model and policy version that
chose it, so a new version classifies that mail again) and IDs of each folder's loaded window (plus Gmail header metadata and history cursor) so a restart shows mail at once and
Gmail re-reads only what changed. Delete it to clear it. There is no telemetry or remote classification. Drafts saved explicitly or before
sending persist locally, including attachment bytes, without additional application-level encryption. OS swap, browser extensions
and screenshots are part of the local machine's privacy boundary.

The workbench data directory (`data_dir/workbench/`) stores, in `accounts.json`, registry connection settings
(including IMAP usernames) and hashed mailbox identities; in `workflow.sqlite`, account-scoped hashed message IDs, manual categories, sender rules (a keyed hash of each address, never the address), legacy completion flags and revisions, which categories are bundled and pane widths; in `cache.sqlite`, the list cache; and for learning (`learning.sqlite`, `personal.json`), each model decision's scores and yes/no facts plus the personal head learned from your categories (numbers only, derived from your mail: keep them private). Explicitly saved drafts contain recipients, body and attachment content in the private local draft store
(`mail/mail.sqlite`); operation receipts may include rejected SMTP recipient addresses for recovery. OAuth credentials
(`accounts/`) and the private launch URL (`launch.json`) are private files; IMAP passwords use the OS vault. Logs exclude mail content.
Read the full [privacy model](privacy.md).

Provider network access is still needed to fetch mail. On-device means local classification/organization,
not offline downloading. The existing Gmail labeling service runs independently. Contacts from one account
are not used to classify another account.

## Recovery and verification

Expired mailbox authorization: **⋯ → Reauthorize this mailbox**. If the local app session expires, reopen
Email Town from its launcher. For transient list failures, **Retry mail load** keeps the current session,
filters, selection and drafts; a failed preview offers **Retry message**. These controls retry reads,
not uncertain sends. Recovery clears the stale notice without taking focus from an editor you moved to.
Restore connectivity before retrying, or let the background sync recover.
Unavailable OS vault: IMAP enrollment stops without plaintext fallback. Model failure: preview and manual
classification remain available.

```sh
uv run --extra dev pytest tests/test_desktop_*.py
uv run --extra dev --with playwright playwright install chromium webkit
uv run --extra dev --with playwright python scripts/check_desktop_ui.py
uv run --extra dev --with playwright python scripts/check_mail_ui.py
uv run --extra dev --with playwright python scripts/check_layout_ui.py
uv run --extra dev --with playwright python scripts/check_recovery_ui.py
uv run --extra dev --with playwright python scripts/check_outbox_layout_ui.py
uv run --extra dev --with playwright python scripts/check_list_reconciliation_ui.py
# Also run WebKit; this does not replace native macOS acceptance:
EMAIL_TOWN_UI_ENGINE=webkit uv run --extra dev --with playwright python scripts/check_desktop_ui.py
EMAIL_TOWN_UI_ENGINE=webkit uv run --extra dev --with playwright python scripts/check_mail_ui.py
```

Browser tests use clearly labeled synthetic mail from two accounts and exercise actual loopback HTTP actions,
filters, plaintext preview, category undo and persistence, keyboard use, and four viewport widths (390, 768,
1280 and 1440 px) in light/dark themes. They do not establish live OAuth/provider behavior for unauthorized accounts. Real cross-provider
acceptance still needs separately authorized accounts, new-mail/expiry/reconnection checks and client feedback tests.
Mail-action tests use mock providers (compose, attachments, drafts, reply, bulk Trash and Restore, uncertain
sends); they do not prove a real provider round trip, which needs an authorized test mailbox.
