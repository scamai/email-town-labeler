# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Before 1.0, minor versions may
contain breaking changes; they are called out.

## [Unreleased]

### Added

- **Undo for Archive and Trash.** For 5 seconds after Archive or Trash, the toast offers **Undo**; the change is held
  back from the mailbox until then, so Undo sends nothing and the messages come straight back. Quitting inside the
  window sends the change at once rather than dropping it.

- Builds can ship Google sign-in: `scripts/build_app.py --google-client <Desktop OAuth client JSON>` puts the
  company project's client into the app (never into the public repository), so connecting a Gmail account
  needs no client file of the user's own. While the project is unverified, only its test users can sign in.

- The workbench guide says which mailboxes a downloaded copy can connect today (app passwords: Gmail, iCloud,
  Yahoo, QQ and others) and which wait for a shipped sign-in client (Google browser sign-in, Microsoft).

- desktop.log records Done, Reopen, category corrections and their undo: which control (`via=preview`,
  `toolbar`, `category-dialog`), which messages by key prefix, and that the mailbox was not changed.

### Changed

- **First run explains itself.** With no mailbox connected, the list welcomes you and offers one button, **Add an
  email account**; sync status, refresh and Compose stay hidden until there is a mailbox. **Add account** opens on the
  route that works in this copy (the email-address form with app passwords when no Google sign-in client ships), with
  the address focused and server settings folded until an unknown domain needs them. The search ✕ shows only when
  there is a search or filter to clear.

- **Done is Archive.** The local Done folder, **Mark done** and **Reopen** are gone: being done with a message is
  **Archive** (`e`, and now `d`), which leaves the Inbox in the mailbox too, as Gmail's Done does, so Email Town and
  the mailbox never disagree. Mail marked done before shows in the Inbox again until it is archived.

- The workbench is laid out like Outlook: a list row is 60 px instead of about 112 px (measured on the
  synthetic fixture at 1440 px), so about twice as many messages fit. Folders, including Drafts and a **Done**
  folder that replaces the Pending / Completed / All switch, move from tabs to the folder pane. Search
  moves to the top bar. The list header becomes one row: date range, filter chips, count. Rows are two
  fixed lines with an avatar that turns into the checkbox, the sender's name without the address, and the
  category on the subject line; unread rows are bold with a blue edge bar. Each folder, category and
  account shows one number, its unread mail; the "automatic", "awaiting classification" and per-entry
  "N unread" texts, the duplicate "All accounts" entry and the per-row account name with one account, the
  "Unclassified" chip and the scope breadcrumb are gone.
- Both pane borders can be dragged (or moved with ←/→) to resize; widths are kept per device.

### Added

- Messages with an HTML version open formatted, as in Gmail: the sender's layout, buttons and inline
  images instead of a text version full of "Image" placeholders. The HTML is sanitized to an allowlist and
  shown in a sandboxed frame that cannot run scripts, submit forms or connect anywhere except to load
  images. External images stay hidden until **Show images**; links open in the system browser; **Plain
  text** switches back. Keyboard shortcuts keep working after clicking into a message.

- `desktop.log` records what happened to each message, by an 8-character key prefix: opens and how
  (click, keys, Shift+J, auto-advance), selection, shortcut keys, mailbox actions with per-message results,
  each open-to-read decision, and read/star changes reported by the mailbox on sync. Page events are
  filtered to short tokens, so no subject, address or text can reach the log.

### Fixed

- Passing over messages with j/k no longer reads them: as in Gmail's reading pane, a message is marked read
  after 1.5 s on it. Mark unread on the open message sticks for that open, even inside the delay.
- A state answer produced just before an open's mark-read could arrive after it and show the open message
  unread again for a moment (seen in a real session's log); the page now holds its read flag until a newer
  answer arrives.
- The formatted view has no frame line, its text lines up with the sender, and hidden external images leave
  a blank space instead of a broken-image box.
- Opening a message no longer fails when its automatic mark-read lands while the body is being read:
  the preview uses the body it just loaded instead of looking the cache up a second time.

- Transient mail-list and message-preview failures offer explicit retry controls without restarting or
  replacing the session. Recovery keeps filters, selected rows, scroll and drafts; late preview results
  cannot overwrite another message, and retries do not steal focus after the user starts typing elsewhere.
  Slow failed previews clear their loading note, while expired sessions retain the launcher recovery path.

- Background syncing and queued mail actions keep the list in place: routine sync no longer inserts
  a notice above the list, and long progress text no longer expands the footer or hides shortcuts.
  Full progress text remains available on hover, with a visible syncing status in narrow windows.

- Opening mail and explicitly marking it read or unread now share one ordered background queue, so an
  earlier automatic read cannot overwrite a later unread action. Pending flags remain temporary overlays;
  failed writes restore the confirmed state even if a background refresh ran while the write was pending.

- Queued read/unread and star actions on the same message keep their latest state while earlier
  actions finish, including failures and background refreshes, instead of flashing back to old flags.

- Message rows now reconcile in place by account-scoped message ID instead of being destroyed and rebuilt
  on every open, read, selection, or background update. Unchanged rows and their controls retain DOM identity,
  scroll position and geometry; repeated keys keep focus, removed rows advance it, and undo restores the
  reopened row. Row actions resolve current state when clicked rather than retaining stale handlers.

### Changed

- Inbox now opens on Pending mail. Done removes a message from that view and places it in the clearly named
  Completed view; Reopen restores it without archiving, deleting, or removing category tags. Date, account,
  category, completion, and search filters compose, while titles state the resulting scope. Loaded unread
  counts now follow that scope for date sections, accounts, categories, and the active folder. Category
  statistics distinguish automatic classifications, user corrections, and awaiting classification instead
  of presenting "0 wrong" as evidence of accuracy. An all-done Pending view offers a View completed action,
  and configured empty folders no longer suggest adding an account. Failed or paused accounts are
  distinguished from empty folders, while other accounts keep their loaded mail. Folder tabs keep their
  height when the last unread badge disappears, preventing a roughly 10 px list jump on narrow windows.
- Messages open without "Reading message…". Like a desktop client, Email Town now reads ahead: the newest 30
  Gmail bodies after a list loads, and, whenever you open a message, the three below it and the one above in
  one batched request. Opened and prefetched bodies are kept (300 on the server, 150 in the page), the
  reading pane and the reply/attachment code share one request per message, and the "Reading message…"
  note appears only when a body really takes longer than 0.3 s. A cold Gmail start still fits the
  per-minute quota budget with room for about 16 opens.
- Bulk actions no longer make you wait. Archive, Trash, Restore, read/unread and star now take effect in the
  list at once and the mailbox writes run in a background queue, one action after another; the footer says
  what is still going ("Archiving 12 of 40"). A message that fails comes back to the list, with the error.
  You can keep working and switch folders meanwhile, and a fourth action queues instead of being refused.
- The message list gets the height back. Its header (folder tabs, a large "Inbox" title, search, select-all
  bar and the "Load more" bar) took about 40% of the list pane; it is now two slim rows, search above and
  select-all · title · count below, and "Load more mail" sits under the list. The first message starts
  about 150 px from the top of the pane instead of about 480 px.
- Category dots in the sidebar now use one clear hue each (the Gmail label hues), instead of the dark text
  colours, where brown, maroon, purple, teal and green looked alike at 8 px. Each dot keeps at least 3:1
  contrast in light and dark mode, and "Unclassified" is a grey ring rather than one more colour.

### Added

- `desktop.log` now says where the time goes. Every API action is logged with its path, status and duration
  (never message IDs, query strings or bodies); each bulk mail action logs its message count, results, total
  time, time spent waiting for the account and in provider calls; each list load logs its folder, provider,
  message count and duration, and when a load was discarded and restarted.

## [0.1.6] - 2026-11-09

### Fixed

- The list no longer jitters under the pointer. Hovering a row (or tabbing into it) swapped the time for
  the Archive / Trash / read controls, which made its title line 4.3 px taller and moved every row below;
  the controls now float over the end of that line, on the row's own colour, and the time keeps its space.
- Opening an unread message no longer resizes its row. A bold subject could wrap onto a second line where
  the regular one did not (21–23 px at some lengths), so the list jumped just after the click; unread
  subjects are now drawn heavier without changing glyph widths, and the unread dot sits in the left margin,
  so read and unread text start in the same place.
- `scripts/check_layout_stability_ui.py` points at and tabs through every control at 1280, 1000 and 390 px
  in WebKit and Chromium and fails if anything else moves (105 shifts before, none after), and sweeps
  subject lengths for read/unread size changes.

## [0.1.5] - 2026-11-08

### Per-category counts

- Each category in the sidebar shows its loaded count with "N done · M wrong" under the name, and All
  categories totals them. "Wrong" counts messages that classification (the local model or a mailbox label)
  put in a category and you moved to another; moving one back clears it. Unchecked mail is not counted,
  so it is a lower bound. The state snapshot carries `auto_category` for corrected messages.
  `scripts/check_category_stats_ui.py` checks the page's immediate update and the server snapshot.

### Reading pane, laid out as in Gmail

- Icon toolbar (Archive, Trash, More) with "N of M" and newer/older arrows; the subject with its category,
  account and unread chips; the sender as avatar, name and address, the date on the right, and the
  recipients folded into "to me, Li and 12 others ▾" with the full From / To / Cc / Reply-To on demand.
- The body is plain text with clickable links (new tab, no opener), a dimmed signature, and the quoted
  history at the end folded behind "•••" — whether the signature comes before the quote or after it, and
  without dropping text when a message has two signatures. Mail text is never parsed as HTML.
- Attachments are cards (type, name on two lines at most, readable size) above Reply / Reply all /
  Forward, all aligned with the sender's name; phones drop the indent and move the date under the sender.
- Styles live in `reader.css`; the page markup no longer carries an inline style, which the app's
  Content-Security-Policy refused. `scripts/check_reader_ui.py` checks all of the above in WebKit and
  Chromium, and a unit test keeps inline styles out of the page.

### Fixed

- The app no longer slides up and leaves an empty band below it. Each message row's screen-reader preview
  escaped the list, so with 100 messages the page was ~12,700 px tall inside an 840 px window; a trackpad
  scroll over a short message or the footer scrolled the whole app. Rows now contain their previews, and
  the app shell clips anything that still escapes. `scripts/check_page_scroll_ui.py` checks both guards
  separately at desktop, narrow and phone widths in WebKit and Chromium.
- The selection survives Mark read/unread and star, as in Gmail; only messages that leave the folder
  (archive, Trash, restore) drop out. Before, finishing an action removed its messages from the selection,
  including ones selected while it ran: Shift+J twice right after starring showed 3 rows highlighted but
  "2 selected", and the next bulk action skipped one.
- Gmail: a second action on the same message before the next sync (Mark unread right after opening it,
  or undoing a Trash) no longer fails with "Message is no longer in this account's current list". The
  message's row stays cached after a write; the next sync still re-reads it, because Gmail's history
  reports the app's own label changes.

## [0.1.4] - 2026-11-08

### Row controls and auto-advance, as in Gmail

- Hovering a row (or focusing into it) shows Archive / Trash (Restore in Trash) / Mark read-unread in place of the time,
  and every row has a star toggle; they act without opening the message and run the toolbar's own action path.
  Touch screens show only the star; read-only or paused accounts show neither.
- Archiving or trashing the open message opens the next, older one in a wide window; a narrow one returns to the list.
- Choosing a folder while another switch or mail action is still running is no longer dropped: the last folder
  chosen opens when it finishes.

### Selecting messages, as in Gmail and desktop mail clients

- Shift-click selects a range from the last clicked message (rows or checkboxes, selecting or deselecting);
  ⌘/Ctrl-click adds or removes one message without opening it.
- A ▾ menu beside "Select all loaded" selects All, None, Read, Unread, Starred or Unstarred; Shift+j/k extends the
  selection, `*` then a/n/r/u/s/t selects by state, Esc clears it. Ranges follow the list as shown and stop at the
  500-message action limit.
- Mail-action keys pressed while another action is still running are no longer dropped: they wait, run in the order
  pressed, and act on the messages that were open or selected when each key was pressed ("s" toggles by the
  message's state when it runs).

### Read and unread, as in Gmail

- Opening a message marks it read in its mailbox: shown at once, written in the background, put back if the write
  fails. A list read that is older than the write no longer flips it back. "Mark unread" keeps the open message
  unread until it is opened again. Accounts without write permission, or paused, are left unchanged.
- Unread rows read as bold sender, subject and time with a blue dot (the "Unread" chip is gone from rows); the open
  folder's tab and the window title show the unread count.

## [0.1.3] - 2026-11-08

### Keyboard shortcuts

- Gmail-style single keys (j/k, e, #, s, Shift U/I, x, c, r, a, f, /, g then i/s/a/t, ?) and Apple Mail ⌘
  combinations (⌘N, ⌘R, ⌘⇧R, ⌘⇧F, ⌘F, ⌘Enter to send; Ctrl elsewhere). Each clicks the same button a pointer
  would, so disabled controls and Trash's confirmation still apply. Single keys never fire while typing in a text
  field, while an input method is composing, or under a dialog. `?` or the footer shows the list.
- The macOS app's menus add Undo/Redo (⌘Z, ⇧⌘Z) and a Window menu with Minimize (⌘M) and Zoom to pywebview's
  defaults.
- `scripts/check_shortcuts_ui.py` checks every shortcut against the real server and a synthetic mailbox in WebKit
  (the macOS app's engine) and Chromium.

### Desktop app on Windows

- Fix two defects that made the 0.1.2 Windows build fail its checks: the bundle dropped Gmail's discovery
  document (a path filter compared Windows `\\` paths against `/`), and the window passed a PNG icon that
  WinForms rejects, ending the app as its window opened. The app CI builds on macos-15 (macos-14 images are
  retired) and keeps the smoke test's logs when it fails.

## [0.1.2] - 2026-11-08

### Desktop default model

- With no `config.toml` (a fresh desktop app) the workbench classifies with the published on-device model
  `town-modernbert-int8` instead of the CLI's keyword-only `rules`, downloading it once (263 MB, sha256-checked)
  on first use. The list shows "Downloading the on-device classifier" meanwhile. A `config.toml` model always wins.

### macOS release signing

- `scripts/release_macos.py` signs the built app with the company Developer ID (hardened runtime, secure
  timestamp, deepest first, never `--deep`), notarizes and staples both the app and its disk image, checks
  Gatekeeper and writes a release receipt. It refuses to choose between several Developer ID identities.
  Adapted from scamai/earlet; see docs/releasing.md.

### Desktop app

- Email Town as a double-click app, no terminal, Python or uv: `dist/Email Town.app` (macOS 14+, Apple
  Silicon), a Windows folder with `Email Town.exe`, and a Linux folder (system GTK 3 and WebKitGTK 4.1).
  The workbench opens in a native window (pywebview: WKWebView, WebView2, WebKitGTK) instead of a browser
  tab; closing the window stops the workbench. If `desktop` already serves the same data folder, the app
  opens a window on it and leaves it running. Original-message links and sign-in pages open in the
  default browser; attachment downloads ask where to save.
- Windows and Linux builds are unsigned: SmartScreen asks for one extra confirmation on first open. The macOS
  release is a disk image signed with the company Developer ID and notarized (see macOS release signing); the
  CI-built macOS zip is a test artifact only ([steps](docs/desktop-workbench.en.md#download-the-app)). The model is not
  bundled; a configured catalog model is downloaded on first use, as before.
- `email-town` (the `[app]` extra) opens the same window from source. `scripts/build_app.py` builds the app
  with PyInstaller (`app-build` dependency group); on an Apple M4 Pro it reported 11 s, 136 MB, a 49 MB zip.
  `scripts/smoke_app.py` checks a build on a new temporary data folder: bundle self-check, server, second
  start, quit, no process left.
- The model worker now also starts inside the frozen app, where `sys.executable` is the app binary: the
  app re-enters itself with `--email-town-model-worker` instead of `python -m`.
- A frozen app whose built-in OpenSSL CA path does not exist on the machine (e.g. a bundle built on Ubuntu,
  run on Fedora) uses its bundled certifi CAs; it never overrides a CA file the system has.
- New workflow `Desktop app` builds and smoke-tests macOS, Windows and Linux. It runs only by hand or on
  a `v*` tag (never on push or pull request: hosted minutes are a cost decision), and attaches the Windows
  and Linux zips to the tag's GitHub release; the macOS asset is the notarized disk image.

### Desktop account setup

- Typing an address for Other email fills IMAP/SMTP settings for Gmail, iCloud, Yahoo, AOL, Fastmail, Zoho and
  QQ Mail and links to where the app password is created; Outlook.com addresses are routed to Microsoft sign-in.
- Import your own Google Desktop OAuth client file in the Add account dialog instead of copying it into the
  configuration directory; the dialog says up front whether Google sign-in is available.
- Support a Google sign-in client and Microsoft app ID shipped with the app (empty in this release); a user's own
  client file still takes precedence.
- The display name is optional; unnamed accounts are listed by their mailbox address.

### Desktop list cache

- Cache each folder's loaded list on disk (`workbench/cache.sqlite`, mode 0600: subject, sender, date,
  read/star state, category and IDs; Gmail header metadata and history cursor; never bodies or snippets).
  A restart shows the last list at once, keeps local model categories, and Gmail re-reads only what its
  history says changed instead of ~2,000 quota units per folder. docs/privacy.md is updated: this replaces
  the earlier "no mail-content disk cache" statement for list fields.

### Desktop loading speed

- Budget Gmail reads in quota units the way Gmail meters them (6,000 per user per minute, shared with the
  CLI service): the workbench may spend 4,800 units of a minute at once instead of trickling 240 metadata
  reads a minute, so a cold 100-row list (2,000 units) no longer waits ~12.5 s for its second batch.
  Rows appear 25 at a time, newest first; throttled reads retry 4 times instead of 2.
- Keep each folder's last list and Gmail metadata across folder switches: returning to Inbox shows its rows
  immediately and revalidates through Gmail history (0.5 s measured) instead of re-reading 100 messages.
- Read bodies in batches over one request (Gmail) or one login (IMAP), classify each batch with one model
  call, and read ahead the newest 10 bodies per Microsoft/IMAP account so the first opens are instant
  (not for Gmail, where a body costs 60 quota units and an open takes about 0.1 s).
- Read IMAP list headers with one FETCH per 200 messages instead of one round trip per message.
- A preview waits for an account that is busy syncing instead of failing after 1 s.
- Replace the 5-second UI poll with a long-poll that answers as soon as local state changes.
- Do not send Sent mail to the local model (shown as "Not classified (sent mail)"): classifying a Gmail
  Sent folder read 100 bodies, 6,000 quota units, the whole per-user minute.

### Desktop accounts

- Show the auto-enrolled default Gmail account by its mailbox address instead of a generated name
  ("当前 Gmail" / "Primary Gmail"); names the user typed are unchanged and nothing new is stored.

### Desktop startup

- Bind the local workbench directly to numeric loopback without reverse DNS, so an unavailable
  system hostname resolver cannot block the launcher readiness handshake.

### Desktop layout

- Reorganize mail navigation, message rows and preview actions with shared gutters and control sizes.
  Contextual batch actions and More menus separate mailbox changes from local task organization.
- Replace stacked tablet previews with a focused reading view and Back navigation; constrain menus
  to the viewport and provide a mobile filter drawer with refresh and keyboard containment.
- Align compose/account fields, strengthen input boundaries, keep Send/Save visible while composing,
  and wrap long attachment names without splitting their Remove controls.
- Replace stale loading screens on expired sessions with a clear reconnection message.
- Add synthetic responsive/keyboard/layout checks, including 320px reflow and short compose windows.

### Desktop mail operations

- Display CID-linked raster images below the message body for Gmail, Outlook and IMAP, including
  unnamed inline attachments. External HTTPS images have a per-preview Load external images button;
  switching messages cancels pending downloads and releases image previews. Sender HTML is not rendered.
- Keep the open preview's Unread badge in sync with mailbox state after read/unread actions
  and background refreshes, matching the message list without reopening the message.
- Compose, reply, reply all and forward from a selected account, attach/download files,
  and save private on-device drafts. Gmail and Graph use their APIs; IMAP accounts can configure TLS SMTP.
- Explicit read/unread, star/unstar, archive, Trash and restore actions sync to the original mailboxes,
  with bounded multi-account selections and Inbox/Sent/Archive/Trash views.
- Durable operation receipts block duplicate requests; interrupted sends remain unconfirmed,
  partial SMTP delivery retains the draft, and IMAP moves never use global EXPUNGE.
- Expanded OAuth permissions require explicit reconnection for previously read-only grants.
  Live provider send/delete acceptance requires dedicated authorized test mailboxes.


### Added

- English-first on-device mail workbench with consistent category colors across navigation,
  message badges and previews, including dark and high-contrast appearances. Category IDs and
  mailbox labels remain unchanged; existing account names and message content are preserved.
- Email Town envelope-and-house vector mark, browser favicon and reproducible macOS launcher icon
  (`scripts/build_desktop_brand.py`). The local launcher installs the packaged icon.

- Client workflow tools: `labels check [--json]` audits Gmail visibility; explicit
  `labels show [--dry-run]` creates missing configured labels and makes them visible without
  changing messages or unrelated labels. Dry-run/check can run alongside the engine; actual
  repair takes the account lock. No automatic visibility override at startup.
- `triage [--plan] [--view action|today|backlog|new-sender|unclassified] [--date YYYY-MM-DD]
  [--timezone IANA] [--limit N] [--json]`: offline Gmail queries or bounded live message counts,
  retaining old backlog and unclassified mail. Uses local calendar boundaries, including DST;
  no message content, state database or model needed. Counts are not task-completion state.
- Optional independent background service names: `service install|status|uninstall --name NAME`,
  `setup --install-service --service-name NAME`, and `status|doctor --service-name NAME`.
  Pair each name with its own `--home`; unnamed service identities remain unchanged. Relative
  account homes/log paths are made absolute when generating services. See
  [daily client workflows](docs/client-workflow.md) for migration and validation boundaries.

### Fixed

- **The README's privacy promises now match the code.** "It will never" said the only network
  traffic was the Gmail API, a model download and the update check, and left out that the
  `ollama_choice` backend sends each message's From, Subject and the start of its body (2,000
  characters by default) to the Ollama server you configure, on this machine unless you opt in.
  It said the local database holds only ids, label names and numbers, but it also keeps your own
  account address and the addresses you have sent mail to (the contacts table), both since 0.1.0,
  and when each message arrived, since 0.1.1. README, `docs/privacy.md`, `docs/architecture.md`,
  `docs/design-spec.md`, `docs/models.md` and CONTRIBUTING.md now say so. Nothing in the code
  changed.

### Changed

- **Private reporting.** Report security and privacy problems through GitHub private
  vulnerability reporting (Security tab → Report a vulnerability) or to contact@scam.ai with the
  subject line `[SECURITY][Email Town]`; security@scam.ai is no longer listed. SECURITY.md follows
  Reality Inc.'s organization-wide policy: acknowledgment within 2 business days and an initial
  assessment within 5 (it said 3 and 10), the same safe harbor, and no bug bounty.
- The bug report form says that issues are public, asks for `doctor --redact` output instead of
  plain `doctor` (which prints your account address and home paths), and asks you to replace
  message ids in log lines. The issue chooser, CONTRIBUTING.md and CODE_OF_CONDUCT.md point at
  the private channels.

## [0.1.1] - 2026-11-03

### Added

- **Update check.** After `setup`, `status`, `doctor`, `model list` or `model pull` in a
  terminal, email-town-labeler fetches a public `latest.json` from its GitHub Releases at most
  once a day (cached in `<data dir>/update-check.json`, failures too) and prints one line on
  stderr when a newer version exists, with the command that installs it from this repository's
  tag. It installs nothing, sends no mail data, account, install ID or version (user agent
  `email-town-labeler`, no cookie or query string), gives up after 2 s, and never changes an
  exit code. The first check says what it fetches and how to turn it off. Never from `run`,
  `once`, `backfill`, `--version`, `--json`, a non-terminal or a development version; `run`
  logs one line at start-up if an earlier check found a newer version. Off with
  `[updates] check = false`, `EMAIL_TOWN_LABELER_NO_UPDATE_CHECK=1`, `DO_NOT_TRACK=1` or `CI`.
  **This changes a promise:** the README used to say "no update checks"; it now describes this
  check ([privacy.md](docs/privacy.md#update-check)). The release workflow attaches
  `latest.json` to every `vX.Y.Z` release and checks that `releases/latest` serves it.
- `email-town-labeler labels rename-prefix NEW [--dry-run]` renames the parent label and every
  label under `label_prefix` in place (Gmail `labels.patch`, same ids), so messages keep their
  labels and recorded decisions and corrections stay valid, then writes the new `label_prefix`.
  It refuses when labels under `NEW/` are in the way, and holds the instance lock, so stop the
  service first.
- `setup` warns, and asks before going on, when labels it did not make already exist under the
  prefix: everything under the prefix counts as email-town-labeler's own, and `undo` removes it.

- `town-modernbert-int8`, an optional catalog model: the default `town-modernbert` with int8
  weights (onnxruntime dynamic quantization). On an Apple M4 Pro it peaked at 1.52 GB while
  loaded instead of 2.96 GB, and 347 of 350 real inbound emails from one mailbox got the same
  final label as with the float32 model (agreement, not accuracy). A 263 MB download in the
  v0.1.0 release. `town-modernbert` stays the default. New `scripts/quantize_onnx.py` makes such
  a package from any `onnx_sequence_classifier` package and records its parity on made-up emails.
  [Model card](docs/model-cards/town-modernbert-int8.md).
- The background service gives a large model's memory back when there is no mail. Under `run`,
  the ML backends (`embedding_probe`, `onnx_classifier`, `hf_sequence_classifier`,
  `onnx_sequence_classifier`) run in a child process (`models/worker.py`) that ends after
  `[model] keep_loaded_minutes` (default 10) without new mail and starts again on the next mail.
  With `town-modernbert` the idle service held about 3 GB; now about 40 MB (measured on an
  Apple M4 Pro, macOS). Unloading in-process returned only about half, hence the process.
  `-1` keeps the old behavior. `registry.resolve_loadable` / `registry.load_manifest` split
  `load_model` in two; `MissingExtraError` now pickles.

- Gmail label colors. Class labels get the same colors as Town's labels of the same name by
  default (`taxonomy.DEFAULT_LABEL_COLORS`; `unknown` and the `<prefix>` parent stay
  uncolored). New labels are created colored; `setup` and service start patch existing class
  labels under `label_prefix` whose color is missing or differs, and never touch labels outside
  the prefix. Override per class under `[label_colors]` (`{ background, text }` or `false`), or
  turn it off with `[gmail] label_colors = false`. A color Gmail rejects (400, outside its
  palette) is logged and the label stays uncolored. `GmailClient.list_labels` now returns a
  label's `color`; new `GmailClient.set_label_color`.
- **Improving from use, for every model (including `town-modernbert`).** Each decision now
  stores the model's scores (log-probabilities) and eight yes/no features (schema v4), so
  `learn.py` trains without re-reading mail or re-running the model. Signals: your corrections
  and removals, Town's labels on mail we decided (`[learn] teacher = "auto"`: used when Town's
  labels exist in the account; `[teacher_labels]` renames them), Town leaving mail unlabeled, and
  (weakly) labels kept for a week. Reading/archiving is never used. A small personal head on the
  scores (anchored to the unchanged model) is activated only when it beats the model on 5-fold
  held-out signals; `action_threshold` / `promotional_threshold` are re-tuned from the same
  signals (action-needed recall first, nothing important in promotional; explicit config wins).
  Stored as `<data dir>/personal.json`, reloaded by the running service when it changes.
  Decisions also store when the message arrived (schema v5): mail decided long after it arrived
  (a `backfill` of old mail) gives no "Town left it unlabeled" or "label kept" signals.
- `email-town-labeler learn [--dry-run] [--reset] [--export FILE] [--json]`; `personalize` runs
  `learn` for models without a trainable head, and `personalize --reset` is the escape hatch.
  The service learns on its own every `interval_hours` with `min_new_signals` new signals
  (`[learn] auto`). `status` shows signal counts, the personal head and its held-out score, and
  thresholds with the source `learned`. New `[learn]` and `[teacher_labels]` config sections.

- Published default model `town-modernbert` (ModernBERT-large, ONNX, `[onnx]` extra; 824 MB) and
  `town-modernbert-torch` (`[embed]`; 635 MB) in a built-in catalog (`models/catalog.py`):
  `model pull town-modernbert [--use]`, `[model] ref = "town-modernbert"`; `model list` shows
  catalog entries as installed / not installed with sizes. Trained on one person's consenting
  inbox; see `docs/model-cards/town-modernbert.md` and `docs/privacy.md`.
- `hub.fetch_url(..., sha256=)`: the archive is verified before extraction and refused on a
  mismatch (catalog downloads always pin it). Download progress on stderr.
  `EMAIL_TOWN_LABELER_CATALOG_BASE_URL` replaces the catalog URL prefix (tests, mirrors, offline).
- A model's `config.recommended_policy` sets `action_threshold` / `promotional_threshold` unless
  `config.toml` sets them; `status`, `doctor` and `model info` show the effective values and
  their source (model / config / default). `setup` writes those two keys commented out.
- `setup` offers `town-modernbert` (download size, required extra) and falls back to `rules`,
  with the install command, when the `[onnx]` extra is missing; `--yes` does the same.
- Docker images build with the `onnx` extra by default.

- Model backends `hf_sequence_classifier` (a fine-tuned transformers sequence classifier stored
  in the package; `[embed]`) and `onnx_sequence_classifier` (the same as one ONNX graph;
  `[onnx]`). `scripts/package_model.py --backend hf_sequence_classifier` packages a
  `save_pretrained` checkpoint (bf16 weights when they match float32 on made-up mail), and
  `scripts/export_onnx.py --seq-classifier` exports it to ONNX with a parity check.
- `[policy]` config: `action_threshold` (default 0.2), `promotional_threshold` (0.6),
  `protect_known_contacts` (true), `new_sender` (true), `contacts_scan_days` (730),
  `contacts_scan_limit` (5000). Decision order: policy, then `min_confidence` (not for
  `action needed`, which uses `action_threshold`), then the new-sender rule, then
  `unknown_action`.
- `new sender` label for `unknown` Primary-tab mail from addresses you have never sent mail to.
- Contacts: lower-cased To/Cc/Bcc addresses of your sent mail, scanned at setup
  (`format=metadata`, headers only) and kept current from history. `email-town-labeler contacts
  rebuild | count | list --yes-show-addresses`. `status` shows the count.
- `GmailClient.get_metadata(ids, headers)`, batched and paced on its own bucket.

### Changed

- **The default `label_prefix` is now `e`** (labels like `e/action needed`), not
  `email-town-labeler`. A `config.toml` that `setup` wrote keeps its prefix; to switch an existing
  mailbox, stop the service and run `email-town-labeler labels rename-prefix e`. A config without
  a `label_prefix` line switches to `e` and starts a second set of labels: add
  `label_prefix = "email-town-labeler"` under `[gmail]` to keep the old one.
- State schema v3: a `contacts` table, and `decisions.model_cls` (the model's own class) next to
  `cls` (the final class). Decisions made before v3 have no `model_cls`.

### Fixed

- **Polls no longer fail on a dead connection after the computer sleeps or changes networks.**
  httplib2 re-raises `BrokenPipeError` / `ConnectionResetError` / `TimeoutError` without closing
  the pooled connection, so the next poll failed on it again (seen as `poll failed
  (BrokenPipeError, 2 in a row)`). `GmailClient` now treats any transport error (no HTTP response)
  by closing the pool and retrying on a fresh connection, three times within about 4 s, separately
  from the rate-limit retries; this covers single requests and batches. Only a network that stays
  down still fails a poll.
- `status` showed the last poll error forever, as `<error> at <epoch seconds>`, even after the
  service had recovered. It now shows `last error` only while polls are failing, with the time
  and how many polls in a row failed, and an unhealthy status names it. `status --json`
  `last_error` is now `{error, at, failures_in_a_row}` or `null`. Old stored errors are read too.
- A Google Cloud project without the Gmail API enabled made `setup` (and any other command)
  stop with Google's raw 403 `HttpError`, and made `doctor` say "check your network". A command
  that stops on it, and `doctor`, now say in one line that the Gmail API is not enabled in
  project N and link the page with its Enable button. The `run` service still logs it as
  `HttpError 403` and retries. New `gmail.client.api_disabled_project`.

## [0.1.0] - 2026-10-23

First public version.

### Added

- Gmail polling via the history API with bootstrap, history-expiry recovery and backoff.
- Seven-class labeling under a configurable prefix (`email-town-labeler/` by default); never touches
  other labels, never archives, deletes or marks read.
- Pluggable models: `model.json` manifest with sha256-checked files; backends `rules`
  (default, no ML dependencies), `embedding_probe` (`[embed]`), `onnx_classifier` (`[onnx]`)
  and `ollama_choice`.
- Model packages from local directories, Hugging Face Hub (`hf:org/repo@rev`) or https archives.
- Feedback capture from label corrections, and `personalize` to refit a model head locally.
- CLI: `setup`, `login [--headless]`, `run`, `once`, `backfill`, `status`, `undo`,
  `model list|pull|info`, `personalize`, `service install|uninstall|status`, `doctor`.
- Background service for launchd, systemd `--user` and Windows Task Scheduler.
- Docker image (non-root, `/data` volume) and Compose file.
- Documentation: Google Cloud setup, privacy and threat model, models, configuration,
  troubleshooting, architecture.
- `status --redact` / `doctor --redact` mask your address and home paths for pasting;
  `gcp-setup --print` prints the Google Cloud commands; `--home` / `-v` also work after the
  command.
- A single-instance lock per data directory for `run`, `once`, `backfill` and `undo`.

### Security

- `ollama_choice` refuses a non-loopback `url` from a model package unless the user opts in
  (`[model] allow_remote_endpoint = true` or `EMAIL_TOWN_LABELER_ALLOW_REMOTE_ENDPOINT=1`).
- `embedding_probe` passes only an allowlist of package `st_kwargs` and always sets
  `trust_remote_code=False`; manifest paths must stay inside the package directory; archive
  extraction has per-file and total size caps.
- The copied `client_secret.json` is written with mode 0600.

### Changed

- History-expiry recovery scans from the last successful poll (at most `[engine]
  recovery_max_days`, default 30, never before setup) instead of a fixed 2 days.
- `status` (and the Docker healthcheck) is unhealthy when no poll has succeeded 15 minutes
  after setup.
- Not on PyPI yet: install from GitHub. The PyPI publish job is off until the name is decided.

[Unreleased]: https://github.com/scamai/email-town-labeler/compare/v0.1.6...HEAD
[0.1.6]: https://github.com/scamai/email-town-labeler/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/scamai/email-town-labeler/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/scamai/email-town-labeler/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/scamai/email-town-labeler/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/scamai/email-town-labeler/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/scamai/email-town-labeler/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/scamai/email-town-labeler/releases/tag/v0.1.0
