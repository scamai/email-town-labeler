# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Before 1.0, minor versions may
contain breaking changes; they are called out.

## [Unreleased]

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

[Unreleased]: https://github.com/scamai/email-town-labeler/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/scamai/email-town-labeler/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/scamai/email-town-labeler/releases/tag/v0.1.0
