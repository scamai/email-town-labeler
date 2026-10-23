# Design spec

The module contracts of email-town-labeler, for contributors. [architecture.md](architecture.md) explains
how the pieces fit together.

Self-hosted, private Gmail auto-labeler. Watches a Gmail inbox, classifies every new inbound
email with a small local model, and adds a label under `email-town-labeler/…`. Nothing about the mail
leaves the user's machine except Gmail API calls to Google itself.

Working name `email-town-labeler`, Python package `email_town_labeler`, CLI `email-town-labeler`. MIT. Python ≥3.10.
Runs on macOS, Windows and Linux; Docker for servers.

## Non-negotiables

1. **Privacy.** Never log, print or send email content (subject, sender, snippet, body, addresses)
   anywhere. The only addresses stored are the contacts (lower-cased recipients of mail the user
   sent); only `contacts list --yes-show-addresses` prints them. Logs carry message ids, classes, probabilities, counts. Tests use made-up mail only.
   No telemetry. Model downloads are the only non-Google network calls, and only on request.
2. **Never destructive.** The tool only ever adds/removes labels under its own prefix. It never
   archives, deletes, marks read, or touches other labels. `undo` removes only its own labels.
3. **Model-agnostic.** The model is a plug-in (manifest + backend). The infrastructure must work
   end to end with the built-in `rules` model (no ML deps) so CI needs no weights.
4. **Cross-platform.** pathlib everywhere, no shell-isms, platformdirs for paths, no POSIX-only APIs
   outside `service.py` and `lock.py` (which picks `msvcrt` or `fcntl` per platform).
5. **Small core.** Core deps: google-api-python-client, google-auth-oauthlib, platformdirs, numpy,
   tomli (py<3.11). ML deps are extras: `[embed]` (sentence-transformers, torch), `[onnx]`
   (onnxruntime, tokenizers).

## Layout

```
src/email_town_labeler/
  __init__.py        __version__
  types.py           EmailRecord (TypedDict), Prediction, HistoryEvent
  taxonomy.py        CLASSES (model classes), LABEL_CLASSES (+ new_sender), display names, descriptions
  features.py        email_text(rec) — the one text rendering models see
  config.py          Config load/save (TOML), paths
  state.py           SQLite Store (decisions, feedback, contacts, kv)
  policy.py          pure: model probabilities → final class (thresholds, contact guard, new sender)
  contacts.py        pure: bare lower-cased addresses from From / To / Cc / Bcc headers
  engine.py          sync loop: history → classify → policy → label; contacts; feedback capture
  cli.py             argparse CLI
  service.py         background service install (launchd/systemd/schtasks)
  lock.py            single-instance file lock per data dir (msvcrt / fcntl)
  gmail/auth.py      OAuth (local-server + headless URL/paste flows)
  gmail/client.py    GmailClient: retrying wrapper over the API
  gmail/parse.py     raw message → EmailRecord
  gmail/labels.py    LabelMap: our class ↔ Gmail label id
  models/base.py     Classifier protocol, ModelManifest
  models/registry.py load_model(ref) → Classifier
  models/hub.py      fetch model packages (HF Hub / https URL, sha256-checked; archive pin + progress)
  models/catalog.py  published models by name (town-modernbert): URL, archive sha256, size, extra
  models/backends/rules.py            no-ML baseline (Gmail categories + headers)
  models/backends/embedding_probe.py  sentence-transformers embedder + numpy softmax head
  models/backends/onnx_classifier.py  ONNX encoder + head, onnxruntime only
  models/backends/hf_sequence_classifier.py    fine-tuned transformers classifier inside the package
  models/backends/onnx_sequence_classifier.py  the same as one ONNX graph, onnxruntime only
  models/backends/ollama_choice.py    local LLM via Ollama HTTP (optional)
  personalize.py     refit the head from the user's own corrections
tests/
  fakes/fake_service.py   googleapiclient-shaped in-memory Gmail (history semantics)
  fakes/fake_client.py    in-memory GmailClient (same interface)
docker/ Dockerfile, compose.yaml; docs/; scripts/gcp_setup.sh; .github/workflows/ci.yml
```

## Contracts (code against these exactly)

### types.py
`EmailRecord` dict keys: `id, thread_id, label_ids, internal_date (ms), history_id, size_estimate,
snippet, headers (lower-case name → value), body_text, body_source, has_calendar, attachments`.
`Prediction(cls: str, prob: float, probs: dict[str, float])`.
`HistoryEvent(kind: "added"|"label_added"|"label_removed", message_id: str, label_ids: list[str],
message_label_ids: list[str])` — `label_ids` = labels added/removed by this event;
`message_label_ids` = the message's labels after the event (as Gmail reports).

### gmail/client.py
```python
class HistoryExpired(Exception): ...           # Gmail 404 on startHistoryId
class GmailClient:
    def __init__(self, service, *, max_retries: int = 6, sleep=time.sleep): ...
    @classmethod
    def from_credentials(cls, creds) -> "GmailClient": ...
    def profile(self) -> dict                    # {"emailAddress", "historyId", "messagesTotal"}
    def list_message_ids(self, *, query: str | None = None, label_ids: list[str] | None = None,
                         max_results: int = 100) -> list[str]        # newest first, paginates
    def get_records(self, ids: list[str]) -> list[EmailRecord]       # format=full, parsed; batched;
                                                                     # missing (404) ids skipped
    def get_metadata(self, ids: list[str], headers: list[str]) -> list[EmailRecord]
        # format=metadata + metadataHeaders: id, thread_id, label_ids, internal_date, history_id and
        # only those headers (lower-cased keys); batched like get_records, own pace
        # (max_metadata_per_minute, default 240)
    def get_label_ids(self, id: str) -> list[str]                    # format=minimal
    def list_labels(self) -> list[dict]          # [{"id", "name", "type"}]
    def create_label(self, name: str) -> dict
    def modify(self, id: str, *, add: list[str] = (), remove: list[str] = ()) -> None
    def history(self, start_history_id: str) -> tuple[list[HistoryEvent], str]
        # all pages; historyTypes messageAdded,labelAdded,labelRemoved; returns new historyId
        # raises HistoryExpired
```
Retries 429 / 5xx / 403 rateLimitExceeded|userRateLimitExceeded with exponential backoff + jitter.

### gmail/auth.py
```python
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
def load_credentials(token_path: Path) -> Credentials | None      # refreshes + rewrites if needed
def login_local(client_secret: Path, token_path: Path, *, open_browser=True) -> Credentials
def headless_auth_url(client_secret: Path, pending_path: Path) -> str     # PKCE, redirect localhost
def headless_finish(client_secret: Path, pending_path: Path, token_path: Path, redirect_url: str) -> Credentials
```
Token files written with 0600 where the OS supports it.

### gmail/labels.py
```python
class LabelMap:
    def __init__(self, client: GmailClient, prefix: str, names: dict[str, str]): ...
        # names: class -> display name, e.g. {"action_needed": "action needed", ...}
    def ensure(self, cls: str) -> str            # creates "<prefix>/<name>" (and parent) if missing
    def class_of(self, label_id: str) -> str | None
    def our_label_ids(self) -> set[str]
```

### policy.py
```python
def apply_policy(pred: Prediction, *, known_contact: bool, cfg) -> Prediction
    # action_needed iff p[action_needed] >= action_threshold; else top non-action class, promotional
    # only if p >= promotional_threshold (else next best); known contact -> never promotional (best
    # non-promotional, non-action class). Missing/NaN/negative probs = 0; a 0-prob result -> unknown.
    # Ties: the model's own cls, then CLASSES order. Returned prob = probs[cls]; probs = the model's.
def confident_class(pred, cfg) -> str      # unknown below the bar: action_threshold for action_needed,
                                           # min_confidence otherwise
def new_sender_class(cls, *, primary: bool, new_address: bool, cfg) -> str
def decide(pred, *, known_contact, new_address, primary, cfg) -> Decision(pred, cls)
```
Order: policy → min_confidence → new sender → unknown_action. `new_sender` is never a model class.

### models/base.py
`ModelManifest` (from `model.json`), `Classifier` protocol: `.manifest`, `.predict(records) ->
list[Prediction]` (probs over `manifest.classes`, always containing every class in `CLASSES`).

### models/registry.py
`load_model(ref: str, *, models_dir: Path, device: str = "cpu", allow_remote_endpoint: bool = False)
-> Classifier`. `allow_remote_endpoint` is the user's opt-in (`Config.remote_endpoint_allowed()`:
`[model] allow_remote_endpoint` or `EMAIL_TOWN_LABELER_ALLOW_REMOTE_ENDPOINT=1`), passed to loaders that
accept it; without it `ollama_choice` refuses non-loopback hosts. A package can never set it.
Package-supplied values never run code: `embedding_probe` allowlists `st_kwargs` and forces
`trust_remote_code=False`; `hf_sequence_classifier` loads only from its package
(`local_files_only`, safetensors only, `trust_remote_code=False`, `auto_map` refused); every manifest path goes through `hub.safe_relpath` and must resolve
inside the package dir; archive extraction is capped per file and in total.
`ref` forms: `rules` (builtin), a catalog name (`town-modernbert`: the installed copy, else a
download pinned by the archive's sha256; the `[onnx]` extra is checked before downloading), a local
directory containing `model.json`, `hf:<org>/<repo>[@rev]`, or a name installed under
`models_dir/<name>/`. `hub.fetch_url(url, models_dir, sha256=...)` verifies an archive before
extracting it; `EMAIL_TOWN_LABELER_CATALOG_BASE_URL` swaps the catalog's URL prefix (tests, mirrors).
A manifest's `config.recommended_policy` (`action_threshold`, `promotional_threshold`) applies to
the keys config.toml does not set: `Config._explicit` records the keys loaded from the file and
`Engine` uses `cfg.for_model(model.manifest)`. Unknown backend → clear error naming the extra
to install (`pip install "email-town-labeler[embed]"`).

### state.py
```python
class Store:
    def __init__(self, path: Path): ...          # sqlite, WAL, schema versioned
    def get(self, key: str, default=None) / set(self, key: str, value: str)
    def record_decision(self, msg_id, cls, prob, probs, model_id, applied: bool, *, dry_run=False,
                        model_cls: str | None = None) -> None   # cls = final class; model_cls, prob,
                                                                # probs = what the model said
    def decision(self, msg_id) -> dict | None
    def recent_decisions(self, n: int = 20) -> list[dict]
    def record_feedback(self, msg_id, predicted: str | None, corrected: str | None) -> None
    def feedback(self) -> list[dict]
    def add_contacts(self, sent: Mapping[str, int | None]) -> int     # address -> first sent ms; new count
    def replace_contacts(self, sent) -> int / known_contacts(addrs) -> set[str] / contacts_count() -> int
    def stats(self) -> dict                      # incl. "contacts" (a count)
```
Schema v3: `contacts(address TEXT PRIMARY KEY, first_sent_ms INTEGER)` (lower-cased, nothing else) and
`decisions.model_cls`.

### engine.py
```python
class Engine:
    def __init__(self, client, labels: LabelMap, model: Classifier, store: Store, cfg: Config, *, clock=time.time): ...
    def sync_once(self) -> dict      # one poll: history since stored id (bootstrap from profile on
                                     # first run; on HistoryExpired fall back to an inbox scan);
                                     # classify eligible new mail; capture feedback; return counts
    def run_forever(self, stop: threading.Event | None = None) -> None   # poll every cfg.poll_seconds
    def backfill(self, *, days: int | None = None, limit: int | None = None) -> dict
    def undo(self) -> int            # remove our labels from every message that has one
    def rebuild_contacts(self) -> dict   # SENT newer_than contacts_scan_days, ≤ contacts_scan_limit
    def ensure_contacts(self) -> None    # scan once (bootstrap, first poll, backfill) if the policy uses it
```
Eligible = in INBOX, not SENT/DRAFT/SPAM/TRASH, no label under our prefix, not already decided,
not carrying any label in `cfg.skip_label_names`. Each prediction goes through `policy.decide`
(known contact = sender in contacts or the account's own address; new address = contacts scanned and
sender not known; primary = `CATEGORY_PERSONAL`); then `cfg.unknown_action` ("label" →
`<prefix>/unknown`, "skip" → record only). `new_sender` is always applied (unless dry-run).
Contacts: `bootstrap()` stores the history pointer, then scans SENT; each poll first adds the
recipients of history messages added with (or labelAdded) `SENT`, before classifying; recovery also
scans `SENT after:` the recovery start.
History expiry: scan inbox mail `after:` the start of the last successful poll (Store
`last_ok_poll_start`, minus an hour of slack), capped at `cfg.recovery_max_days` and never before
Store `bootstrap_ts` (written by `bootstrap()`): mail that predates setup stays backfill-only. Dry-run records, never
modifies. Feedback: if the user removes our label and/or adds a different one of ours on a message
we decided, record `(predicted, corrected)`; corrected=None when only removed.

### config.py
TOML at `paths.config_dir/config.toml`; `EMAIL_TOWN_LABELER_HOME` overrides all paths (Docker: `/data`).
```toml
[gmail]
label_prefix = "email-town-labeler"
poll_seconds = 60
skip_label_names = []        # e.g. labels another tool owns
[model]
ref = "rules"
min_confidence = 0.55
unknown_action = "skip"
device = "cpu"
allow_remote_endpoint = false
[engine]
dry_run = false
recovery_max_days = 30
[policy]
action_threshold = 0.2       # 0 < x <= 1
promotional_threshold = 0.6  # 0 .. 1
protect_known_contacts = true
new_sender = true
contacts_scan_days = 730     # 1 .. 3650
contacts_scan_limit = 5000   # 0 .. 100000
```
Files: `config_dir/client_secret.json`, `config_dir/token.json`, `data_dir/state.sqlite`,
`data_dir/models/`, `data_dir/logs/`.

### cli.py (`email-town-labeler …`)
`setup` (wizard: client secret → login → pick model → dry run on 5 newest → offer service install),
`login [--headless]`, `run` (foreground daemon), `once`, `backfill [--days N] [--limit N] [--dry-run]`,
`status [--json] [--redact]`, `undo`, `model list|pull <ref>|info`, `personalize`,
`service install|uninstall|status`, `contacts rebuild|count [--json]|list --yes-show-addresses`,
`doctor [--redact]` (checks: config, token, scopes, Gmail
reachable, model loads, labels exist), `gcp-setup --print`, `--version`. `--home` / `-v` work
before or after the command. `run`, `once`, `backfill` and `undo` hold `lock.InstanceLock(data_dir)`;
a second instance exits 1. `status` is unhealthy when: not logged in, or no successful poll and
setup (`bootstrap_ts`, else the token's mtime) is older than 15 min, or the last successful poll is
older than `max(10 × poll_seconds, 15 min)`.
Exit codes: 0 ok, 1 error, 2 usage.

## Test strategy
- Unit tests per module with fakes; no network, no weights, runs in <30 s on all OSes.
- `tests/test_e2e.py`: real `GmailClient` + `fake_service` + `rules` model + `Engine` through a
  simulated day: bootstrap → new mail (with injected API failures) → label → user correction →
  feedback → expired history → restart → backfill → trash/spam → undo; asserts no double labels,
  nothing outside the prefix touched, and no mail content in logs.
- ML backends: tests skip cleanly when extras are missing; a tiny randomly-initialised head tests
  the numpy path.
