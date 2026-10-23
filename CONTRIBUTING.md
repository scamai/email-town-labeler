# Contributing to email-town-labeler

Thanks for helping. Bug reports, documentation fixes, tests, new backends and model proposals
are all welcome. Please read this page first, especially [the privacy rule](#the-privacy-rule).

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## The privacy rule

email-town-labeler exists to keep mail private. Contributors are held to the same standard as the
code.

**Never put real email, in any form, into this repository or its issue tracker.** That
includes subjects, senders, recipients, addresses, snippets, bodies, headers, message or
thread ids from anyone's actual mail, screenshots of an actual inbox, and model weights or embeddings
derived from real mail. It applies to:

- **tests and fixtures**: use made-up mail only (`alice@example.com`, `Order #12345 shipped`).
  Use `example.com` / `example.org` / `example.net` domains (RFC 2606), never a real company's
  domain with a real-looking message;
- **issues, pull requests and discussions**: describe the shape of the problem ("a newsletter
  with a `List-Id` header and an HTML-only body") or write a synthetic reproduction;
- **logs and debug output you paste**: `doctor` and `status` are designed to be safe, but
  read what you paste first;
- **code**: never log, print, raise, or persist subject, sender, addresses, snippet or body.
  Log message ids, class names, probabilities, counts and error types only. Exception
  messages must not embed content either. Reviewers will block PRs that break this.

If you accidentally post private data, edit it out and tell a maintainer
(security@scam.ai) so the history can be purged.

## Development setup

You need Python 3.10 or newer. Everything runs without a Google account and without model
weights.

With [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/scamai/email-town-labeler && cd email-town-labeler
uv venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
uv pip install -e ".[dev]"
```

With pip:

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Optional ML backends: add `embed` and/or `onnx` to the extras (`-e ".[dev,embed]"`). On Linux,
install CPU torch first to avoid multi-GB CUDA wheels:
`pip install --index-url https://download.pytorch.org/whl/cpu torch`.

## Running tests

```bash
pytest                      # whole suite; no network, no weights, < 30 s
pytest tests/test_e2e.py    # end to end: real GmailClient + in-memory fake Gmail + rules model
ruff check .                # lint
ruff format .               # format
python -m build             # sdist + wheel into dist/
docker build -f docker/Dockerfile -t email-town-labeler:dev .   # container image
```

CI runs lint, a format check, tests, a wheel build and a smoke test of the installed wheel on
Ubuntu, macOS and Windows with Python 3.10, 3.12 and 3.13, plus a Docker build. Please make
sure `pytest`, `ruff check .` and `ruff format --check .` pass locally.

Tests that need an optional extra must skip cleanly when it is missing
(`pytest.importorskip("sentence_transformers")`).

## Code guidelines

- Contracts between modules are in [docs/design-spec.md](docs/design-spec.md). If you change one, update it in
  the same PR.
- Cross-platform: `pathlib`, `platformdirs`, no shell-isms; OS-specific code only in
  `service.py` (and the per-platform lock in `lock.py`).
- Core dependencies stay small. ML libraries go into extras and are imported lazily.
- email-town-labeler never touches labels outside its own prefix, and never archives, deletes or
  marks mail read. Changes that would need a broader action will not be accepted.
- `features.email_text` is the model input contract. Changing it changes every model's input:
  it needs a strong reason and a note in CHANGELOG.md.
- Add a CHANGELOG.md entry under "Unreleased" for user-visible changes.

## Pull requests

1. For anything larger than a small fix, open an issue first to agree on the approach.
2. Keep PRs focused; include tests.
3. Fill in the PR template, including the privacy checklist.

## Proposing a model

We want a good default model, and we hold it to a high bar. To propose one:

1. **Open an issue** with the "Model proposal" template before doing a lot of work.
2. Make sure it meets every item in
   [docs/models.md, Requirements for a shipped model](docs/models.md#requirements-for-a-shipped-model):
   training data with consent for this use, a model card from
   [MODEL_CARD_TEMPLATE.md](MODEL_CARD_TEMPLATE.md), a license, held-out metrics computed with
   the [evaluation protocol](docs/models.md#evaluation-methodology), pinned revisions and sha256
   hashes.
3. Host the package yourself (Hugging Face Hub or an https archive). Do not commit weights to
   this repository.
4. Include the evaluation script or notebook so maintainers can reproduce the numbers.

Models trained on a private mailbox, including your own, are accepted only with the mailbox
owner's explicit, informed consent to publishing the weights, and a model card that says so
plainly: that the other people in that mail were not individually asked, and that the model may
memorise fragments of it. The default `town-modernbert` was published on exactly these terms
([privacy.md](docs/privacy.md#the-published-model-and-whose-mail-it-learned-from)). Never
submit a model trained on mail without its owner's consent.

## New backends

A backend is a module with `load(manifest, *, device, **kw) -> Classifier` (see
`src/email_town_labeler/models/base.py`). Built-ins live in `src/email_town_labeler/models/backends/`; external
ones can register through the `email_town_labeler.backends` entry-point group. A new built-in backend
needs: lazy imports of its dependencies with a clear "install `email-town-labeler[extra]`" error,
tests that skip without the extra, and documentation in `docs/models.md`.

## Releases (maintainers)

1. Update `__version__` in `src/email_town_labeler/__init__.py` and move "Unreleased" in CHANGELOG.md
   under the new version.
2. Tag `vX.Y.Z` and push the tag. `.github/workflows/release.yml` checks the tag matches the
   version, publishes to PyPI via trusted publishing, pushes images to GHCR, and creates a
   GitHub release.
