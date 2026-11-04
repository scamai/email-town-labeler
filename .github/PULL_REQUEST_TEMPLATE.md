## What and why

<!-- What does this change, and why? Link the issue if there is one. -->

## How it was tested

<!-- Commands run, new tests. -->

## Checklist

- [ ] `pytest`, `ruff check .` and `ruff format --check .` pass locally
- [ ] Tests added or updated
- [ ] CHANGELOG.md updated under "Unreleased" (user-visible changes)
- [ ] Docs / docs/design-spec.md updated if behavior or a contract changed

### Privacy (required)

- [ ] No real email anywhere in this PR: code, tests, fixtures, screenshots, description
- [ ] No new log line, exception message or output prints subject, sender, addresses, snippet or body
- [ ] No new network destination other than Google APIs, user-requested model downloads, the update check's `latest.json` (docs/privacy.md#update-check) and, for `ollama_choice`, the user's Ollama server (docs/privacy.md#model-endpoints)
- [ ] Label changes remain limited to labels under the configured prefix
