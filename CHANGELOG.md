# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Before 1.0, minor versions may
contain breaking changes; they are called out.

## [Unreleased]

### Added

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

- A Google Cloud project without the Gmail API enabled made `setup` (and any other command)
  stop with Google's raw 403 `HttpError`, and made `doctor` say "check your network". A command
  that stops on it, and `doctor`, now say in one line that the Gmail API is not enabled in
  project N and link the page with its Enable button. The `run` service still logs it as
  `HttpError 403` and retries. New `gmail.client.api_disabled_project`.

## [0.1.0] - unreleased

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

[Unreleased]: https://github.com/scamai/email-town-labeler/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/scamai/email-town-labeler/releases/tag/v0.1.0
