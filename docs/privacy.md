English · [简体中文工作台说明](desktop-workbench.md)

[Back to README](../README.md)

# Privacy and threat model

[Desktop workbench](#local-desktop-workbench) · [Data flows](#what-goes-where) · [Disk storage](#what-is-stored-on-disk) · [Logs](#logs-never-contain-content)

email-town-labeler exists so that you can have your mail auto-labeled without handing it to a third
party. This page states exactly what goes where, what the tool can and cannot do, and what is
stored on disk. If the code ever disagrees with this page, that is a bug: please report it
(see [SECURITY.md](../SECURITY.md)).

## Local desktop workbench

`desktop` is an explicit display surface for private mail, unlike CLI status/log output. It reads
Gmail, Microsoft Graph or TLS IMAP and displays subjects, senders, plaintext bodies and referenced images through
an authenticated loopback HTTP server in the user's browser. Manual categories and completion are local.
The desktop workbench can explicitly send/reply/forward, change read/unread and starred state, archive,
trash and restore messages, and upload/download attachments; these operations require the relevant account
grant and are separate from the label-only CLI service.
Classification is shared on-device; remote model endpoints are refused even if allowed by the CLI config.

Inline raster images referenced by Content-ID are fetched through the authenticated mailbox connection
and displayed as browser Blob URLs. Raw email HTML, scripts and styles are never rendered. External HTTPS
images require a click for each preview and load directly from the browser: image hosts can see the
browser's IP and the requested URL (including tracking identifiers); their cookie and cache policies apply.
Requests omit the referrer and never receive the workbench session token. The server does not proxy
arbitrary URLs. Other image schemes and inline SVG are not displayed.

The process holds bounded previews in RAM (128 × at most 256 KiB of body text plus 50 image references),
including the newest 10 bodies per Microsoft/IMAP account read ahead from the mailbox so they open without waiting.
Inline previews are limited to 8 MiB per image and 32 MiB of Blob data per open message; switching messages
or losing authentication cancels pending loads and revokes those URLs. Bodies are never written to disk.
The list itself is cached on disk so a restart shows mail at once: `workbench/cache.sqlite` (mode 0600) holds the
subject, sender, date, read/star state, category and IDs of the loaded window of each folder (at most 1,000 rows
per account and folder), plus each Gmail account's header metadata (Subject/From/Date, labels) and history cursor.
It holds no bodies, snippets or attachments, has no application-level encryption (rely on full-disk encryption),
and is rewritten after every sync; rows of an account that is no longer connected are deleted on the next start.
Deleting the file is safe and only costs one full reload. Explicitly saved drafts and their attachments are
stored under `workbench/mail` as described below.
Browser extensions, OS swap and screenshots are outside that guarantee.
`workbench/accounts.json` holds names, connection settings (including IMAP usernames), and identity hashes.
`workflow.sqlite` holds account-scoped message hashes, local categories, completion and revisions; `cache.sqlite`
holds the list cache above. Private
drafts, attachments and operation receipts are stored under `workbench/mail`; receipts retain IDs, digests
and status, not sent bodies; partial SMTP delivery receipts also retain accepted/rejected recipient addresses so recovery can avoid duplicate delivery. Failed, unknown or partial sends retain the draft and are not automatically retried.
OAuth tokens are mode-0600 local files; IMAP passwords require an approved OS keyring backend.
`launch.json` is a private capability link. Session authorization uses a custom bearer header and
per-origin browser sessionStorage, never a cookie shared between localhost ports. HTTP request logging is disabled.
Account pause retains mail access and only pauses synchronization; it is not credential revocation.

The providers already hold these messages. Graph and IMAP connections extend the Gmail-only data-flow
table below; there is still no telemetry or third-party classification service. See the
[workbench guide](desktop-workbench.en.md) for grants, limits, startup and recovery.

## What goes where

```mermaid
flowchart LR
  subgraph you["Your machine / container"]
    ij["email-town-labeler process"]
    db[("state.sqlite<br/>ids, classes, probabilities,<br/>your account address,<br/>contacts (addresses you wrote to)")]
    tok[("token.json<br/>OAuth refresh token")]
    mdl[("models/<br/>weights")]
    ij --- db
    ij --- tok
    ij --- mdl
  end
  g["Google: Gmail API<br/>gmail.googleapis.com"]
  hf["Model host (optional)<br/>GitHub release / Hugging Face / https URL"]
  ol["Ollama (optional)<br/>localhost:11434 by default"]
  up["GitHub Releases<br/>latest.json (public)"]
  ij <-- "OAuth + Gmail API calls (HTTPS)" --> g
  ij -. "model download, only on request" .-> hf
  ij -. "local HTTP, only with ollama_choice" .-> ol
  ij -. "update check: at most daily, in a terminal" .-> up
```

| Data flow | When | Contains mail content? |
|---|---|---|
| email-town-labeler to Google (Gmail API, OAuth endpoints) | always | Google already has your mail; email-town-labeler reads it from Google and sends back label changes. |
| Desktop browser to image hosts named in a message | only after clicking Load external images for that preview | The image URL and browser IP are visible to that host; this may signal that the message was opened. No workbench token or referrer is sent. |
| email-town-labeler to GitHub releases, Hugging Face or a model URL | only when you run `model pull` or `setup` downloads a model, or the configured model is not on disk yet; the desktop workbench with no `config.toml` uses `town-modernbert-int8` and downloads it once (263 MB), saying so in the list | No. A plain download of a model package, verified against sha256 hashes (for published models, the archive's hash is pinned in the code too). |
| email-town-labeler to Ollama | for every message it classifies, only if you choose the `ollama_choice` backend | Yes: the From and Subject headers, whether the message has mailing-list headers or a calendar invite, and the body text with its whitespace collapsed, cut to 2,000 characters unless the model package's `max_body_chars` sets another length (`features.py` `email_text`). It goes to the Ollama URL in the model package. Only loopback hosts are allowed unless you opt in (see [Model endpoints](#model-endpoints)). |
| email-town-labeler to GitHub Releases (`latest.json`) | at most once a day, after `setup`, `status`, `doctor`, `model list` or `model pull` in a terminal; never from `run`, `once`, `backfill`, `--json` or a non-terminal (so not from the service or the Docker health check); off with `[updates] check = false` (see [Update check](#update-check)) | No. One HTTPS GET of a public file, with no mail data, account, install ID, version, cookie or query string. |
| Explicit desktop mail actions | only when requested in the app; opening a message marks it read (as Gmail does) on accounts with mail-management permission | Send content/attachments or mail-management requests to the selected account’s Gmail, Microsoft or configured IMAP/SMTP provider. |
| email-town-labeler to anyone else | never | There is no telemetry, analytics, crash reporting, install ID or "phone home". |

### Update check

So that you hear about fixes, the interactive commands look for a newer release, and only tell
you about it:

- **What is sent:** one `GET https://github.com/scamai/email-town-labeler/releases/latest/download/latest.json`
  with the user agent `email-town-labeler` (no version), and no query string, body, cookie,
  token or install ID. GitHub redirects it to the newest release's copy, then to its download
  host (`release-assets.githubusercontent.com`; checked 2026-11-02), which serves
  `{"version": "X.Y.Z"}`. Redirects are followed only to `https://` URLs. GitHub sets cookies
  in its answer; email-town-labeler keeps none, so no request carries one. GitHub sees your IP
  address, the time and the request headers: the user agent, plus `Host`,
  `Accept-Encoding: identity` and `Connection: close`, which Python's `urllib` always sends.
- **When:** after `setup`, `status`, `doctor`, `model list` or `model pull`, when their output goes
  to a terminal and `--json` is not given, at most once every 24 hours. The result, a failure
  too, is cached in `update-check.json` in the data directory. Before the first check
  email-town-labeler prints one line saying what it fetches and how to turn it off. `run` (the
  background service), `once`, `backfill`, `--version`, a development version and the Docker
  image's default command and health check never check; `run` only logs one line at start-up
  if the cache already knows a newer version.
- **What it does with the answer:** prints one line on stderr with the new version, its
  release page and the command that installs it from this repository's tag. It installs
  nothing. `latest.json` is not signed, so only a version of the form `X.Y.Z` is accepted, and
  the URL and command come from the code, never from the file. Failures (offline, a 2-second
  timeout, an unexpected answer) are silent: they show only with `-vv`, and never change a
  command's exit code.
- **Turning it off:** `[updates] check = false` in `config.toml`
  ([configuration.md](configuration.md#updates)), or the environment variable
  `EMAIL_TOWN_LABELER_NO_UPDATE_CHECK=1`. `DO_NOT_TRACK=1` and `CI` (set by most CI systems)
  turn it off too.

### Model endpoints

A model package is data written by someone else, so nothing in it may decide where your mail
goes or what code runs:

- **`ollama_choice` only talks to this machine by default.** Its `url` comes from the
  package's `model.json`, so email-town-labeler refuses any host other than `localhost`,
  `127.0.0.0/8` or `::1` and fails with an error naming the host. To use Ollama on another
  machine you control, opt in yourself: `[model] allow_remote_endpoint = true` in
  `config.toml` ([configuration.md](configuration.md#model)), or the environment variable
  `EMAIL_TOWN_LABELER_ALLOW_REMOTE_ENDPOINT=1`. A package cannot set either. Once you opt in, the
  message text goes to that host: it is then as private as that host and the network path to
  it (use `https://` off your LAN).
- **Packages never run code.** `embedding_probe` passes only an allowlist of
  sentence-transformers options from the package and always sets `trust_remote_code=False`;
  every file path in a manifest must stay inside the package directory; archives are
  extracted with path, link and size checks. See [models.md](models.md).

Classification happens on this machine, on your CPU (or GPU if you configure one): in the
email-town-labeler process or, for a large model under `run`, in a child process it starts
(`models/worker.py`, so the model's memory can be given back when idle). Message text crosses an
operating-system pipe between those two processes, never a file or a network socket. It is held
in memory for the duration of one classification batch and is not written to disk. The one
exception is the `ollama_choice` backend: the model runs in your Ollama server, so the text in
the table above goes to it over HTTP (a loopback connection unless you opted in to another host).

## What is stored on disk

| File | Contents | Contains mail content? |
|---|---|---|
| `config.toml` | your settings | No |
| `client_secret.json` | your OAuth client id/secret from Google Cloud | No |
| `token.json` | OAuth access + refresh token for your Gmail. Mode 0600 on macOS/Linux. | No, but it grants access to your mail: protect it like a password. |
| `state.sqlite` | Gmail message ids, the model's class, probabilities and scores (log-probabilities over the seven classes) and the final class we labeled, eight yes/no facts per decision (known contact, mailing-list headers, Gmail tab, calendar invite), model id, whether a label was applied, when each message arrived (Gmail's internal date) and when we decided it, your corrections (predicted class, corrected class), which class a Town label on a message we decided means (if you use Town), the last Gmail history id, setup, poll and learning timestamps, the error type of the last failed poll, your account address (shown by `status`), counters, and the **contacts** table: lower-cased addresses you have sent mail to, each with the time you first did (see [Contacts](#contacts)) | No subjects, senders, snippets or bodies. The only addresses are your own account address and the contacts table. |
| `personal.json` | the personal head (a 7x16 weight matrix) and learned thresholds from `learn`, with signal counts and held-out scores | Numbers only, no ids or content, but derived from your mail: keep it private. Delete it (or `learn --reset`) to go back to the model as shipped. |
| `models/` | model packages (`model.json` + weights) | Not from your mail, unless you ran `personalize` (the refitted head is derived from it). The published `town-modernbert` was trained on someone else's consenting inbox: see [below](#the-published-model-and-whose-mail-it-learned-from). |
| `logs/` | operational logs | No. See below. |
| `update-check.json` | when the last [update check](#update-check) ran, the newest version it saw, or the type of error | No |

### Contacts

The policy needs to know whether a sender is someone you have written to: such mail is never
labeled `promotional` (`protect_known_contacts`), and mail from anyone else in your Primary tab
can get the `new sender` label (`new_sender`). For that, email-town-labeler keeps a `contacts` table in
`state.sqlite`:

- **What:** lower-cased email addresses from the To, Cc and Bcc headers of mail **you sent**,
  plus the time you first sent to each. No names, no subjects, no other headers, no content.
  Mail you receive never adds anyone.
- **How:** at setup, email-town-labeler lists your sent mail from the last `contacts_scan_days` days (at
  most `contacts_scan_limit` messages) and fetches only those three headers
  (`format=metadata`). After that it adds the recipients of each message you send, as it shows
  up in Gmail's history. Nothing is sent anywhere; this is the same Gmail API access as above.
- **Who sees it:** nobody but you. Logs, `status` and `contacts count` show only how many
  addresses are stored. The one way to print them is `email-town-labeler contacts list
  --yes-show-addresses`.
- **Turning it off:** set both `protect_known_contacts` and `new_sender` to `false` in
  `[policy]`; no contacts are collected from then on. `email-town-labeler contacts rebuild` with
  `contacts_scan_limit = 0` empties the table; deleting `state.sqlite` removes everything.

This is personal data about the people you correspond with: back up and share the data
directory as carefully as the mailbox itself.

Locations per OS are in [configuration.md](configuration.md#files-and-directories). In Docker
everything is in the `/data` volume.

### Logs never contain content

Logs carry message ids, class names, probabilities, counts, timings, HTTP status codes and
error types. They never contain a subject, sender, recipient, address (contacts included),
snippet or body, and neither do exceptions we raise. `status` and `doctor` show nothing about your messages either,
but they do print **your own account address** and local file paths (which usually contain
your user name); `email-town-labeler status --redact` / `email-town-labeler doctor --redact` mask both before you
paste the output anywhere. This rule is enforced in review and in tests (see
[CONTRIBUTING.md](../CONTRIBUTING.md#the-privacy-rule)).

A Gmail message id is an opaque identifier. On its own it reveals nothing, but combined with
access to your mailbox it points at a specific message, so do not post raw ids publicly
either if you consider that sensitive.

## What `gmail.modify` allows, and what email-town-labeler does

Google offers no scope that means "read mail and change labels, nothing else". The closest fit
is `https://www.googleapis.com/auth/gmail.modify`, which Google describes as "Read, compose,
and send emails from your Gmail account", without permanent deletion. That is broader than
what email-town-labeler needs. The consent screen will say so, and it is correct.

What the code actually does with it:

| Allowed by the scope | Does email-town-labeler do it? |
|---|---|
| Read messages and labels | Yes: new inbox messages (to classify), their label ids (to detect your corrections and, if you use Town, Town's labels on mail we decided), and the To/Cc/Bcc headers of mail you sent (to know your contacts). Town's labels are only read, never changed. |
| Create labels | Yes, only under its own prefix (default `e/`). `labels rename-prefix` renames those, and only those. |
| Add / remove labels on messages | Yes, only labels under its own prefix. |
| Touch other labels (INBOX, UNREAD, STARRED, IMPORTANT, CATEGORY_*, your labels) | The desktop workbench can perform explicit mailbox actions; the classifier CLI remains label-only. |
| Trash or delete messages | Desktop workbench supports explicit trash/restore; classifier CLI does not. |
| Send, draft or forward mail | Desktop workbench supports explicit plaintext send/draft/reply/forward; classifier CLI does not. |
| Change settings or filters | No (and the scope does not allow it). |

`email-town-labeler undo` removes only email-town-labeler's own labels from every message that has one. The
API calls are listed in [google-cloud-setup.md](google-cloud-setup.md#quota).

The automatic classifier uses `src/email_town_labeler/gmail/client.py`: `engine.py` adds the class label
resolved by `LabelMap`, and `undo` removes only label IDs under the configured prefix. Explicit desktop
actions use `desktop/providers.py`, including a separate one-shot Gmail write transport to avoid
automatic retries of a potentially accepted send.

## Who else could see your mail

- **Google** holds your mailbox regardless. email-town-labeler does not change that.
- **The machine you run email-town-labeler on.** Anyone with access to `token.json` can read your mail
  until you revoke the token. Run it on a machine you trust, and keep backups of the data
  directory as private as the mailbox itself.
- **Model authors** never see your mail. A model package is data plus a small amount of
  backend configuration; backends are code that ships with email-town-labeler, not with the model.
  A package cannot point a backend at a remote host or make it run code (see
  [Model endpoints](#model-endpoints)).
- **The Ollama host**, if you use `ollama_choice` and opt in to a host other than this machine
  with `allow_remote_endpoint`.

## Revoking access

1. Go to <https://myaccount.google.com/permissions>, find **email-town-labeler** (the app name you
   chose on the consent screen), and click **Delete all connections** / **Remove access**.
2. Delete `token.json`, or the whole data directory / Docker volume.
3. Optional: delete the Google Cloud project, which invalidates every token it issued.
4. Optional: `email-town-labeler undo` first, if you also want the labels gone.

Changing your Google password also revokes tokens that carry Gmail scopes.

## The published model, and whose mail it learned from

The default model, `town-modernbert` (and its twins `town-modernbert-torch`, as PyTorch weights,
and `town-modernbert-int8`, as int8 weights), **was trained on one person's private mail**: 4,176
inbound emails from one Gmail inbox, labeled by a hosted auto-labeling tool. That person
consented to publishing the weights. The [model card](model-cards/town-modernbert.md) says so;
this section spells out what it means.

**For your mail: nothing changes.** The model is a download. It runs on your machine and sends
nothing anywhere; your mail is never used to train or update it. The privacy properties above
hold with it exactly as with `rules`.

**For the people in the training mail, there is a residual risk:**

1. **Models memorise.** Fine-tuned encoders can retain fragments of their training data (names,
   senders, subject lines, rare phrases), and membership-inference attacks can sometimes tell
   whether a given message was in the training set. The weights are a lossy, hard-to-read
   trace of that inbox, not a copy, but not nothing either. Do not use the model to try to
   recover its training data.
2. **Consent covers the mailbox owner, not every sender.** Most of an inbox is other people's
   words. The owner consented; the senders of those 4,176 emails were not individually asked.
   The model was trained only on inbound mail, its output is a class label, and email-town-labeler
   never prints model internals, but the weights are public.
3. **It reflects one inbox.** The labels come from one person's mail and one tool's taxonomy,
   so the model will fit your mail less well than its card's numbers suggest. Treat those
   numbers as an indication of the method, not of what you will see.

If that trade-off is not acceptable to you, use `rules` (`[model] ref = "rules"`), which ships no
weights at all, or a model you train yourself.

### Models trained on your mail stay private

- `email-town-labeler learn` (also run by the service, `[learn] auto`) fits a small personal head
  and thresholds from **your own** corrections and Town's labels, on your machine, from numbers
  stored at decision time (no mail is re-read), and writes `personal.json` in the data directory.
  `learn --export` writes your signals (message ids and numbers) to a file you name.
- `email-town-labeler personalize` (head-based models) refits a small classifier head from
  **your own** corrections, on your machine, and writes it under your `models/` directory.
- Those files are derived from your mail. Treat them as private: do not upload them,
  share them or attach them to an issue. The model card it writes says so.
- The same goes for anything you train yourself from your labels
  ([models.md](models.md#training-your-own-model-from-your-labels)): publishing it would publish
  a trace of your mail and of your correspondents' words. Do it only with the eyes-open consent
  described above, never by accident.
- Contributions of trained models must follow the requirements in
  [models.md](models.md#requirements-for-a-shipped-model).

## Reporting a privacy issue

If you find a code path that exposes mail content outside the documented local preview, draft,
classification and explicitly requested provider operations, treat it as a security issue and report it privately: see [SECURITY.md](../SECURITY.md).
