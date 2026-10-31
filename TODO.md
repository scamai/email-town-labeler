# Unified on-device mail workbench queue

Product objective: explicitly authorized inboxes from many providers appear in one local place,
with shared categories and fast daily handling. Completion and classification remain independent.
The user requested implementation through a usable local app, not a sequence of proposal phases.

## Implemented and exercised locally

- [x] Existing-client Gmail label visibility audit/repair and daily inbox queries.
- [x] Independent named per-account labeling services; default service preserved.
- [x] Authenticated loopback workbench, multi-account registry, Gmail/Graph/TLS IMAP readers.
- [x] One local classifier shared across workbench accounts; no cross-account contact inference.
- [x] Unified inbox, account/category/date/completion filters, subject/sender search, plaintext preview.
- [x] Account-scoped local completion and manual categories, batch actions, atomic stale-safe undo.
- [x] Explicit enrollment, per-account health, pause/resume and identity-checked reauthorization.
- [x] System-vault IMAP passwords, private OAuth files, no mail-content disk cache.
- [x] Start/reopen/stop background process and a reproducible macOS double-click launcher.
- [x] Synthetic two-account browser checks in four widths and both themes; real HTTP and persisted-state checks.
- [x] Existing authorized Gmail read-only runtime: 100 inbox messages loaded, ready, no model error.

## Real-account acceptance still requires those accounts' authorization

- [ ] Connect the user's remaining Gmail / Microsoft / IMAP accounts through the workbench.
  No client credential extraction; each grant must be completed by the mailbox owner.
- [ ] Verify two real accounts simultaneously, new-mail arrival, token expiry and reauthorization;
  synthetic adapter tests do not prove real provider/tenant behavior.
- [ ] Verify IMAP OS vault enrollment on the user's chosen provider and Microsoft tenant device flow.
- [ ] Dedicated test-mailbox round trip: classify → correction in Gmail / Outlook / Apple Mail →
  server label state → existing CLI learning. Read-only native visibility was checked; mutation was not.

## Remaining product work

- [ ] Unified local corrections → optional provider write-back and consented learning; separate from completion.
- [ ] Archive/sent/custom-folder scope, incremental Graph/IMAP sync, and broader history beyond the bounded inbox window.
- [ ] Account disconnect/revocation/removal policy, without erasing other accounts or silently discarding workflow state.
- [ ] Multi-account resource measurements and consented Chinese-language quality evaluation; historical one-mailbox
  model results do not prove new-user recall or false-positive rates.
- [ ] Public distribution: OAuth verification/public client strategy, signed/notarized package, update/rollback/uninstall.
  The local launcher is not a public release or a complete replacement for an email client.

Usage: [本机工作台](docs/desktop-workbench.md) · [English guide](docs/desktop-workbench.en.md).
Existing-client acceptance protocol: [client workflow](docs/client-workflow.md).
