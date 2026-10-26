# Troubleshooting

Start with:

```bash
email-town-labeler doctor     # checks config, token, scopes, Gmail reachability, model, labels
email-town-labeler status     # account, model, last poll, counts per class, service state
```

Neither prints anything about your messages (no subjects, senders or bodies), but **both
include your own email address** (the account line, and `gmail reachable` in `doctor`) and
local paths that usually contain your user name. Before pasting them into an issue, run them
with `--redact`, which masks the address and your home directory:

```bash
email-town-labeler doctor --redact
email-town-labeler status --redact
```

Still glance over the output before posting. See [CONTRIBUTING.md](../CONTRIBUTING.md#the-privacy-rule).

## Login and tokens

| Symptom | Likely cause | Fix |
|---|---|---|
| Stops working after about a week; `invalid_grant`, "Token has been expired or revoked" | External OAuth app in **Testing** status: Google expires its refresh tokens after 7 days | `email-town-labeler login`. To stop it recurring, publish the app to **In production**: [google-cloud-setup.md, step 4](google-cloud-setup.md#4-audience-testing-vs-production-external-only). |
| `invalid_grant` right after changing your Google password | Google revokes tokens with Gmail scopes on password change | `email-town-labeler login` |
| `access_denied`, "has not completed the Google verification process" | Testing status and your address is not a test user | Add it under **Audience > Test users**, or publish the app. |
| "Google hasn't verified this app" | Expected for your own unverified app | **Advanced > Go to email-town-labeler (unsafe)** |
| `redirect_uri_mismatch` | OAuth client type is Web application | Create a **Desktop app** client and re-run `email-town-labeler setup`. |
| `org_internal` | Internal app, account outside the organization | Use an account in that org, or switch the app to External. |
| `admin_policy_enforced` | Workspace admin blocks the Gmail scope | Ask the admin to trust the app's client id. |
| Browser never opens / no desktop (SSH, server, container) | `login` defaults to the local-browser flow | `email-town-labeler login --headless`, then paste the final `http://localhost...` URL. The page failing to load is expected. |
| `Address already in use` during login | Another process holds the loopback port | Retry; or use `--headless`. |
| Scope error in `doctor` | Token was granted without `gmail.modify` (unticked on the consent screen) | `email-town-labeler login` and leave the Gmail permission ticked. |

## Gmail API

| Symptom | Cause | Fix |
|---|---|---|
| "the Gmail API is not enabled in Google Cloud project N" (older versions: `accessNotConfigured`, "Gmail API has not been used in project") | API not enabled | Open the link in the error and click **Enable**, or `gcloud services enable gmail.googleapis.com --project ID`, or the console link in [google-cloud-setup.md](google-cloud-setup.md#2-enable-the-gmail-api). Wait a minute and run the command again. |
| Many `429` / `rateLimitExceeded` lines during `backfill` | Per-user quota (6,000 units/min); each message fetch costs 60 | Expected; email-town-labeler backs off and continues. Use `--limit` / `--days` to keep backfills small. |
| Log says history expired, then an inbox scan | The stored history id is too old (Gmail documents history ids as typically valid for at least a week, occasionally only hours) | Nothing to do. email-town-labeler scans inbox mail received since its last successful poll (at most `[engine] recovery_max_days`, default 30, and never mail from before setup) and continues. |
| Labels do not appear in the Gmail sidebar | Gmail hides nested labels under a collapsed parent | Expand `e` (your `label_prefix`) in the sidebar, or search `label:e-action-needed`. |

## Labels look wrong

- Run with `[engine] dry_run = true` for a few days and look at `email-town-labeler status`.
- Raise `[model] min_confidence` to label less but more accurately.
- Too much or too little `action needed`: raise or lower `[policy] action_threshold`.
  Real mail filed as `promotional`: raise `[policy] promotional_threshold`. See
  [configuration.md](configuration.md#policy).
- `new sender` on someone you know: they are not in your contacts yet (only addresses you have
  *sent* mail to count). It goes away once you reply; `email-town-labeler contacts rebuild` rescans
  older sent mail with the current `contacts_scan_days`.
- Correct labels in Gmail as you go; then run `email-town-labeler personalize` (embedding/ONNX models
  only). See [models.md](models.md#learning-from-your-corrections-personalize).
- Mail you never want touched: put a label on it with a Gmail filter and list that label in
  `[gmail] skip_label_names`.
- Start over: `email-town-labeler undo` removes every `e/...` label, and only those.

## Models

| Symptom | Fix |
|---|---|
| `needs 'sentence_transformers': install it with pip install "email-town-labeler[embed] @ git+https://github.com/scamai/email-town-labeler"` | Install the extra in the same environment: `pipx install --force "email-town-labeler[embed] @ git+https://github.com/scamai/email-town-labeler"` (or `pip install "email-town-labeler[embed] @ git+https://github.com/scamai/email-town-labeler"` in a venv). email-town-labeler is not on PyPI: never `pip install email-town-labeler` from there. In Docker, build with `--build-arg EXTRAS=embed`. |
| sha256 mismatch on `model pull` | The download is corrupt or the package changed upstream. Re-run; if it persists, report it to the model's publisher. Do not edit the hashes. |
| `torch` install pulls gigabytes of CUDA libraries on Linux | Install the CPU wheel first: `pip install --index-url https://download.pytorch.org/whl/cpu torch`, then the extra. |
| `cannot reach Ollama` | Start it with `ollama serve`, and check `config.url` in the model's `model.json`. |
| `refusing to send mail text to '...'` | The model package points `ollama_choice` at a host other than this machine. If it is an Ollama server you control, set `[model] allow_remote_endpoint = true` (or `EMAIL_TOWN_LABELER_ALLOW_REMOTE_ENDPOINT=1`); otherwise do not use that package. See [privacy.md](privacy.md#model-endpoints). |
| `st_kwargs: refusing [...]` / `unsafe path in model package` / `resolves outside the package directory` | The package asks for something email-town-labeler never allows (remote code, files outside the package). Use a different package, or fix its `model.json`. |

## Service

| Symptom | Fix |
|---|---|
| `another email-town-labeler is already running for ...` | `run`, `once`, `backfill` and `undo` take a lock per data directory, so only one runs at a time. Usually the background service holds it: wait for a one-shot command to finish, or `email-town-labeler service uninstall` first (reinstall afterwards). |
| `service status` says not running | `email-town-labeler service uninstall && email-town-labeler service install`, then check the log file under `<data dir>/logs/`. |
| macOS: works in a terminal, not as a service | launchd starts with a minimal environment; reinstall the service from the same shell/venv you run `email-town-labeler` from so the absolute path is recorded. |
| Linux: stops when you log out | systemd user services need lingering: `loginctl enable-linger $USER`. |
| Windows: does not start after reboot | Check Task Scheduler for the `email-town-labeler` task; reinstall with `email-town-labeler service install`. |

## Docker

| Symptom | Fix |
|---|---|
| `Permission denied` on `/data` | Use the named volume from `compose.yaml`. For a bind mount, make the host directory owned by uid 10001 (`sudo chown -R 10001:10001 ./data`), or build with `--build-arg UID=$(id -u) --build-arg GID=$(id -g)`. |
| Container `unhealthy` right after start | Not logged in yet. Copy the client secret and run `docker compose run --rm email-town-labeler login --headless` (see the [README](../README.md#docker)). |
| Container `unhealthy` later | `email-town-labeler status` says why: not logged in; no successful poll yet 15 minutes after setup; or the last successful poll is older than `max(10 x poll_seconds, 15 min)`. Check `docker compose logs`. |
| Login prompt does not accept input | Use `docker compose run` (interactive, TTY), not `docker compose up`. |

## Getting help

Open an issue with the output of `email-town-labeler --version`, `email-town-labeler doctor --redact`, your OS
and Python version, and the relevant log lines. **Never** paste email content, addresses,
`token.json` or `client_secret.json`.
