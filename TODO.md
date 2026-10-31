# Client workflow delivery queue

The immediate product objective is visible automatic classification and faster daily handling
inside existing mail clients. Keep classification feedback separate from task completion.
No publication, account authorization or real-mail mutation is implied by a local code checkpoint.

## Implemented in source; validation recorded at delivery

- [x] Gmail visibility audit reads full label definitions and distinguishes missing/hidden/unknown.
- [x] Explicit visibility repair with dry-run, preserving messages and unrelated labels.
- [x] Daily live inbox views plus offline query generation; old backlog and unclassified mail retained.
- [x] Calendar-day timezone boundaries, bounded counts and message-vs-thread caveats.
- [x] Named background services with per-account homes; original service behavior preserved.

## Next acceptance work

- [ ] Dedicated test-account round trip in Gmail, Outlook for Mac and Apple Mail: classify → client
  correction → server label state → learning feedback. Do not use an arbitrary user's real message
  as test material. Read-only visibility has been verified; writes have not.
- [ ] Adopt a completion convention and test it independently of class labels. No `done` label or
  completion database is designed or shipped yet; never use class removal as a completion shortcut.
- [ ] Exercise two authorized accounts on the real OS scheduler, including stopping one while the
  other continues, OAuth expiry and reconnect. Separate names alone are not an account manager.
- [ ] User trial of fixed folder/search entries: record daily handling steps and missed important
  mail before deciding whether a unified workbench is necessary.

## Follow-on implementation units

- [ ] Account management: inventory of explicitly enrolled homes, private credentials, per-account
  health, validated service identity; first-run and account removal must not touch other accounts.
- [ ] Shared model resources: measure simultaneous workers and design safe sharing before promising
  one model in memory across accounts. Avoid weakening account/data separation.
- [ ] Microsoft Graph: independently authorized test mailbox; account-bound message identity,
  category mapping, incremental sync, retry/re-auth and correction feedback; validate Outlook and
  Apple Mail separately. Existing Gmail-in-Outlook support does not satisfy this item.
- [ ] Lightweight management UI: account setup, status, pause/recovery and client entry guidance;
  no email body cache or send/archive operations without an explicit product decision.
- [ ] Quality: consented per-account and Chinese-language evaluation; report recall/false positives
  separately from label parity. Historical one-mailbox model results are not a new-user benchmark.
- [ ] Distribution: release version, OAuth verification strategy, signing/notarization, updates,
  rollback and uninstall. Current changes are not a signed/publicly released desktop application.

Usage and acceptance protocol: [docs/client-workflow.md](docs/client-workflow.md).
