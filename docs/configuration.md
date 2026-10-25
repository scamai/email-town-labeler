# Configuration

email-town-labeler reads one TOML file. `email-town-labeler setup` writes it for you; edit it by hand to change
anything later, then restart the service (`email-town-labeler service uninstall && email-town-labeler service
install`, or `docker compose restart`).

## Files and directories

| What | macOS | Linux | Windows |
|---|---|---|---|
| config dir: `config.toml`, `client_secret.json`, `token.json` | `~/Library/Application Support/email-town-labeler/` | `~/.config/email-town-labeler/` (`$XDG_CONFIG_HOME`) | `%LOCALAPPDATA%\email-town-labeler\` |
| data dir: `state.sqlite`, `models/`, `logs/` | `~/Library/Application Support/email-town-labeler/` | `~/.local/share/email-town-labeler/` (`$XDG_DATA_HOME`) | `%LOCALAPPDATA%\email-town-labeler\` |

Paths come from [platformdirs](https://pypi.org/project/platformdirs/). `email-town-labeler setup`,
`email-town-labeler doctor` and `email-town-labeler status --json` print the exact paths on your machine.

**`EMAIL_TOWN_LABELER_HOME`** (or the `--home DIR` flag, before or after the command) overrides all of the above: config goes to `$EMAIL_TOWN_LABELER_HOME/config/` and
data to `$EMAIL_TOWN_LABELER_HOME/data/`. The Docker image sets `EMAIL_TOWN_LABELER_HOME=/data`, so inside the
container the files are:

```
/data/config/config.toml
/data/config/client_secret.json
/data/config/token.json
/data/data/state.sqlite
/data/data/models/
/data/data/logs/
/data/hf-cache/            # Hugging Face cache (HF_HOME), only with the embed image
```

`EMAIL_TOWN_LABELER_HOME` is also handy for running two mailboxes side by side, or for trying settings
without touching your real state.

Only one `run`, `once`, `backfill` or `undo` works on a data directory at a time: they hold a
lock on `<data dir>/email-town-labeler.lock`, and a second one exits with status 1 and a message saying
another email-town-labeler is running (for example the background service; stop it first). The OS
releases the lock when the process ends, so a leftover lock file is harmless.

## `config.toml`

All keys with their defaults:

```toml
[gmail]
label_prefix = "email-town-labeler"     # labels are created as "<prefix>/<name>"
poll_seconds = 60              # 10 .. 86400
skip_label_names = []          # never classify mail carrying any of these labels
label_colors = true            # color the class labels (Town's colors unless [label_colors] says otherwise)

[model]
ref = "rules"                  # e.g. "town-modernbert"; see docs/models.md for ref forms
min_confidence = 0.55          # below this, the chosen class becomes "unknown" (not for action needed)
unknown_action = "skip"        # "skip": record only; "label": add <prefix>/unknown
device = "cpu"                 # "cpu" or "cuda" (embed/onnx backends only)
allow_remote_endpoint = false  # true: let ollama_choice send mail text to a non-loopback host
keep_loaded_minutes = 10       # `run`: free a large model's memory after this long without mail (-1 .. 1440)

[engine]
dry_run = false                # true: classify and record, never touch Gmail labels
recovery_max_days = 30         # history expired: rescan at most this many days (1 .. 365)

[policy]
action_threshold = 0.2         # p(action needed) at or above this: label "action needed" (0 < x <= 1)
promotional_threshold = 0.6    # "promotional" only at or above this; else the next best class (0 .. 1)
                               # (leave these two unset to use the model's recommended values)
protect_known_contacts = true  # never "promotional" for someone you have sent mail to
new_sender = true              # label Primary mail with no other label from a stranger "new sender"
contacts_scan_days = 730       # setup reads sent mail this far back to learn your contacts (1 .. 3650)
contacts_scan_limit = 5000     # ...but at most this many sent messages (0 = no scan)

[learn]                        # improving from use (see docs/models.md#improving-from-use-learn)
auto = true                    # the service re-learns on its own
interval_hours = 24            # ...at most this often (1 .. 8760)
min_new_signals = 20           # ...and only with this many new signals since the last run
min_examples = 30              # corrections + removals + teacher labels before a personal head is fit
prior_strength = 10.0          # pull toward the unchanged model (~ this many messages it gets right)
correction_weight = 2.0        # you moved our label to another of ours
removal_weight = 1.0           # you removed our label without adding another ("not that class")
teacher = "auto"               # Town's labels as ground truth: "auto" (if they exist), "on", "off"
teacher_weight = 1.0
teacher_none_weight = 0.5      # Town running but left it unlabeled for teacher_wait_days: unknown
teacher_wait_days = 3.0
confirm_weight = 0.2           # our label kept for confirm_days: weak "that class" (0 = off)
confirm_days = 7.0
tune_policy = true             # re-tune action/promotional thresholds from the same signals
action_recall_target = 0.9     # keep action needed recall at least this
promo_important_max_rate = 0.01  # of mail labeled promotional, at most this may be important
max_examples = 20000           # newest decisions per run (100 .. 1000000)

[labels]                       # optional: rename the Gmail labels per class
# action_needed = "todo"
# newsletters = "reading"

[label_colors]                 # optional: per-class color, or false to leave that label alone
# orders = { background = "#b99aff", text = "#000000" }
# promotional = false
[teacher_labels]               # optional: Town's label names, if you renamed them there
# newsletters = "reading"
```

`setup` and `personalize` write only the `[learn]` keys you set; the rest use the defaults above.

### `[gmail]`

- **`label_prefix`**: parent label for everything email-town-labeler creates. Must not be empty or a
  Gmail system label name. Changing it later does not rename existing labels: run
  `email-town-labeler undo` first if you want a clean switch.
- **`poll_seconds`**: how often to ask Gmail for changes. Each idle poll costs 2 quota units
  (see [google-cloud-setup.md](google-cloud-setup.md#quota)), so 60 s is cheap; going lower
  mostly adds API calls.
- **`skip_label_names`**: label *names* (as shown in Gmail, for example `"Clients/Acme"`).
  Messages carrying any of them are never classified. Use it to stay out of the way of
  another tool or your own filters.
- **`label_colors`** (default `true`): give the class labels Gmail colors (see
  [`[label_colors]`](#label_colors)). `false` turns color management off entirely: labels are
  created uncolored and existing colors are left exactly as they are (not removed).

### `[model]`

- **`ref`**: which model package to use. The built-in default is `rules`, which needs nothing
  else installed; `email-town-labeler setup` offers the published `town-modernbert` instead when
  the `[onnx]` extra is installed. See [models.md](models.md#getting-a-model-modelref-forms).
- **`min_confidence`**: 0..1. Raise it for fewer but more certain labels. It applies to every
  class except `action needed`, which has its own bar (`[policy] action_threshold`).
- **`unknown_action`**: what to do with low-confidence or `unknown` predictions. It never
  affects `new sender`, which is always labeled when it applies.
- **`device`**: `cpu` everywhere. `cuda` needs a CUDA build of torch or onnxruntime.
- **`allow_remote_endpoint`**: default `false`. The `ollama_choice` backend sends message text
  to the URL in the model package, so by default email-town-labeler refuses any host that is not this
  machine (`localhost`, `127.0.0.0/8`, `::1`). Set it to `true` only to use an Ollama server on
  another machine you control; the environment variable `EMAIL_TOWN_LABELER_ALLOW_REMOTE_ENDPOINT=1`
  does the same. Nothing in a model package can turn this on. See
  [privacy.md](privacy.md#model-endpoints).
- **`keep_loaded_minutes`**: default `10`. The background service (`run`) keeps a large model
  (the `embedding_probe`, `onnx_classifier`, `hf_sequence_classifier` and
  `onnx_sequence_classifier` backends; `town-modernbert` uses about 3 GB) in a separate process
  and ends that process after this many minutes without new mail, which gives all of its memory
  back. The next mail starts it again (about a second for `town-modernbert` on an Apple M4 Pro).
  `0` ends it after every poll that used it; `-1` loads the model once inside the service and
  keeps it, as versions before this setting did. `once`, `backfill` and `setup` are unaffected.

### `[engine]`

- **`dry_run`**: useful for the first days with a new model. Decisions show up in
  `email-town-labeler status`, but nothing changes in Gmail. `backfill --dry-run` does the same for a
  single backfill.
- **`recovery_max_days`**: if email-town-labeler was off so long that Gmail no longer has the history
  since its last poll, it scans inbox mail received since that last successful poll instead,
  but never more than this many days back, and never mail from before you set it up (that
  stays `backfill`-only).

### `[policy]`

The model gives every message a probability for each of its seven classes. The policy decides
which one becomes the label, favouring what matters to you: not missing mail that needs you,
and not burying real mail under `promotional`.

**Thresholds recommended by the model.** A model package can recommend its own
`action_threshold` and `promotional_threshold` (`config.recommended_policy` in its `model.json`),
tuned to how that model's probabilities are spread. They are used for each of the two keys you
have **not** set in `config.toml`; a key you set always wins. `setup` writes these two keys
commented out, so the model's values apply until you uncomment one. The published
`town-modernbert` recommends `action_threshold = 0.07` and `promotional_threshold = 0.8`.
`email-town-labeler status` and `doctor` show the values in effect and where each one comes
from: `config`, `learned`, `model` or `default` (the built-in values below), in that order of
precedence.

**Thresholds learned from your signals.** With `[learn] tune_policy` on (the default), `learn`
re-tunes the two thresholds you have not set from your corrections and Town's labels, putting
action-needed recall first and keeping important mail out of `promotional`; see
[models.md](models.md#re-tuned-thresholds). `learn --reset` drops them.

- **`action_threshold`** (default `0.2`): if the model gives `action needed` at least this
  probability, the message is labeled `action needed`, even when another class scored higher.
  Below it, `action needed` is never used. **Lower it to flag more mail as action needed**: you
  miss fewer messages that need you, but get more false alarms. Raise it for fewer, surer
  `action needed` labels. This is also the confidence bar for `action needed`:
  `min_confidence` does not apply to it.
- **`promotional_threshold`** (default `0.6`): `promotional` is used only when the model gives
  it at least this probability; otherwise the message gets the next best class (often
  `updates`). **Raise it to keep uncertain mail out of `promotional`**, which is worth doing if
  you treat that label as "never read". `0` turns this rule off.
- **`protect_known_contacts`** (default `true`): mail from an address you have sent mail to is
  never labeled `promotional`; it gets the best class that is neither `promotional` nor
  `action needed` instead (or nothing, if the model saw nothing else in it).
- **`new_sender`** (default `true`): adds the `new sender` label to mail that would otherwise get
  no label, when it is in Gmail's **Primary** tab and comes from an address you have never sent
  mail to. Mail in Promotions, Social, Updates or Forums, mail from your own address, and mail
  from your contacts never gets it.
- **`contacts_scan_days`**, **`contacts_scan_limit`**: "someone you have sent mail to" comes from
  a contacts list email-town-labeler keeps in `state.sqlite`. At setup it reads the To/Cc/Bcc headers of
  your sent mail from the last `contacts_scan_days` days, at most `contacts_scan_limit` messages
  (headers only, never bodies), then adds the recipients of everything you send from then on.
  `email-town-labeler contacts rebuild` rescans with the current values. When both
  `protect_known_contacts` and `new_sender` are off, no contacts are collected at all. See
  [privacy.md](privacy.md#contacts).

#### How a message gets its label

For each new message, in this order:

1. **Policy.** `action needed` if its probability reaches `action_threshold`. Otherwise the
   highest-scoring other class, except that `promotional` needs `promotional_threshold` (else
   the next best), and is never used for a known contact (`protect_known_contacts`).
2. **`min_confidence`.** If the chosen class's own probability is below `min_confidence`, the
   result is `unknown`. (`action needed` already passed its own bar in step 1.)
3. **New sender.** An `unknown` result for Primary mail from an address you have never written
   to becomes `new sender` (if `new_sender` is on).
4. **`unknown_action`.** An `unknown` result is labeled `<prefix>/unknown` (`"label"`) or only
   recorded (`"skip"`).

Ties go to the model's own top pick, then to the class listed first under `[labels]` below.
`email-town-labeler status` counts messages by this final class; the state database also keeps the
model's own top class for each message, so you can see what the policy changed.

### `[labels]`

Maps a class to a different display name. Names must not contain `/`. Valid classes:
`action_needed`, `newsletters`, `promotional`, `orders`, `travel`, `updates`, `unknown`,
`new_sender`.

### `[label_colors]`

By default the class labels get the same colors Town uses for its labels of the same name:

| class | background | text |
|---|---|---|
| `action_needed` | `#ffad47` (orange) | `#000000` |
| `newsletters` | `#16a766` (green) | `#ffffff` |
| `promotional` | `#f691b3` (pink) | `#000000` |
| `orders` | `#b99aff` (purple) | `#000000` |
| `travel` | `#a4c2f4` (light blue) | `#000000` |
| `updates` | `#fad165` (yellow) | `#000000` |
| `new_sender` | `#4a86e8` (blue) | `#ffffff` |
| `unknown` | none | none |

The `<prefix>` parent label is never colored. Override a class with
`orders = { background = "#rrggbb", text = "#rrggbb" }`, or set it to `false` to leave that
label's color alone. Gmail only accepts colors from its fixed label palette (the swatches in
Gmail's "Label color" menu; the full list is under `color` in the
[labels resource reference](https://developers.google.com/gmail/api/reference/rest/v1/users.labels)).
A color outside it is rejected by Gmail: email-town-labeler logs a warning and leaves the label
uncolored instead of failing.

New class labels are created with their color. `setup` and the service, each time it starts,
also update existing class labels under `label_prefix` whose color is missing or differs from
the config (not in `dry_run`). Labels outside `label_prefix`, including Town's own, are never
changed. If you recolor one of our labels in Gmail, the next start puts the configured color
back; set that class to `false` (or pick your color here) to keep yours.
### `[learn]`

How email-town-labeler improves from use; the full description is in
[models.md](models.md#improving-from-use-learn). Everything runs locally on numbers stored at
decision time; no key here makes anything leave your machine.

- **`auto`**: `run` (the service) checks hourly and learns when `interval_hours` have passed and
  there are `min_new_signals` new signals. `false`: only when you run `email-town-labeler learn`.
- **`min_examples`**, **`prior_strength`**: how much evidence the personal head needs before it is
  fit, and how strongly it is pulled toward the unchanged model. A fitted head is only used if it
  beats the model on held-out signals.
- **`*_weight`**: how much each kind of signal counts. `0` turns a kind off. `confirm_weight`
  (kept labels) is low on purpose: a kept label may just be unread.
- **`teacher`**: `auto` uses Town's labels as ground truth when at least three of its label
  names exist in your account; `on` with any; `off` never. Their names come from
  `[teacher_labels]` (defaults: `action needed`, `newsletters`, `promotional`, `orders`,
  `travel`, `updates`, `new sender`, `awaiting reply`; top-level, case-insensitive). Do not list
  them in `skip_label_names` if you want them used: skipped mail is never decided.
- **`tune_policy`**, **`action_recall_target`**, **`promo_important_max_rate`**: threshold
  re-tuning. Keys set in `[policy]` are never tuned.

### `[teacher_labels]`

Maps a meaning to the Gmail label name your teacher uses. Valid keys: `action_needed`,
`newsletters`, `promotional`, `orders`, `travel`, `updates`, `new_sender` (counts as `unknown`),
`awaiting_reply` (ignored).

## What is eligible for labeling

A message is classified only if all of these hold:

- it is in `INBOX`;
- it is not in `SENT`, `DRAFT`, `SPAM` or `TRASH`;
- it carries no label under `label_prefix` yet (so your manual labels always win);
- email-town-labeler has not decided on it before;
- it carries none of the `skip_label_names`.

Mail that arrived before the first run is left alone unless you run `email-town-labeler backfill`.

## Environment variables

| Variable | Effect |
|---|---|
| `EMAIL_TOWN_LABELER_HOME` | put config and data under one directory (see above) |
| `EMAIL_TOWN_LABELER_ALLOW_REMOTE_ENDPOINT` | `1`: same as `[model] allow_remote_endpoint = true` |
| `HF_HOME` | Hugging Face cache location for `embedding_probe` embedders |
| `HTTPS_PROXY` / `HTTP_PROXY` / `NO_PROXY` | honored by the Google client and model downloads |
