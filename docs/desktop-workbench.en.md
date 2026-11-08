English · [简体中文](desktop-workbench.md)

[Back to README](../README.md)

# On-device unified mail workbench

[Open](#open) · [Connect accounts](#connect-accounts) · [Process mail](#process-mail) · [Appearance](#appearance) · [Privacy](#privacy) · [Recovery and verification](#recovery-and-verification)

The workbench combines explicitly authorized mailboxes into one local list, with Inbox, Sent, Archive and
Trash views, common categories, search and completion state. It supports explicit send/reply/forward,
read/unread, starred, archive, trash/restore and attachment actions. Manual categories and completion remain local.
This is locally runnable source and a browser UI, not a signed/notarized or app-store release.

## Open

```sh
email-town-labeler desktop --background
```

For a fresh install use `uv tool install 'email-town-labeler[onnx,desktop]'`; to use this source checkout,
use `uv tool install --editable '.[onnx,desktop]'`. The desktop extra provides OS credential-vault support
for IMAP. Classification uses the existing default model configuration. Missing weights may need a
model download; mail content is not sent to a remote classifier. The desktop workbench is separate from
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

## Connect accounts

An existing default Gmail authorization is enrolled automatically and listed by its mailbox address. Use **Accounts → + Add** in the sidebar for other
mailboxes, with recognizable display names. The app does not extract Outlook or Apple Mail credentials.

| Provider | Connection | Prerequisite |
|---|---|---|
| Gmail / Workspace | Browser OAuth, Gmail modify (read/send/manage) | Google Desktop OAuth client file in default configuration; [setup guide](google-cloud-setup.md) |
| Another existing Gmail CLI home | Explicit absolute `--home` directory | Its own valid Gmail token |
| Microsoft 365 / Outlook.com | Device code OAuth | Your public-client application ID with device flow enabled; tenant approval may be required |
| iCloud / other IMAP | Verified TLS, username, app-specific password | IMAP enabled and OS credential vault available |

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
the app. This remains a local launcher, not a signed App Store application.

## Privacy

The UI explicitly extends the old CLI's IDs/counts-only output rule: subjects, senders and bodies appear in
the **local browser's memory**. The process retains at most 128 previews (including the newest 10 per Microsoft/IMAP account read ahead), each with at most 256 KiB of body
text plus up to 50 image references. Image blobs are released when switching messages or losing the session.
External images load directly in the browser without a referrer or the workbench bearer token; the remote
host sees the browser's IP and image URL, and its own cookie/cache policies apply. There is no
general mail-content disk cache, telemetry or remote classification. Drafts saved explicitly or before
sending persist locally, including attachment bytes, without additional application-level encryption. OS swap, browser extensions
and screenshots are part of the local machine's privacy boundary.

The workbench data directory stores registry connection settings (including IMAP usernames), hashed mailbox
identities, account-scoped hashed message IDs, manual categories, completion and revisions. Explicitly saved drafts contain recipients, body and attachment content in the private local draft store; operation receipts may include rejected SMTP recipient addresses for recovery. OAuth credentials
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
