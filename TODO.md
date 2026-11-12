# Unified on-device mail workbench queue

Product objective: explicitly authorized inboxes from many providers appear in one local place,
with shared categories and fast daily handling. Completion and classification remain independent.
The user requested implementation through a usable local app, not a sequence of proposal phases.

## Implemented and exercised locally

- [x] Existing-client Gmail label visibility audit/repair and daily inbox queries.
- [x] Independent named per-account labeling services; default service preserved.
- [x] Authenticated loopback workbench, multi-account registry, Gmail/Graph/TLS IMAP readers.
- [x] One local classifier shared across workbench accounts; no cross-account contact inference.
- [x] Unified inbox, account/category/date/completion filters, subject/sender search, plaintext preview, folders and explicit mail actions.
- [x] Account-scoped local completion and manual categories, batch actions, atomic stale-safe undo.
- [x] Explicit enrollment, per-account health, pause/resume and identity-checked reauthorization.
- [x] System-vault IMAP passwords, private OAuth files; explicitly saved drafts in a private local store.
- [x] Start/reopen/stop background process and a reproducible macOS double-click launcher.
- [x] English-first UI and recovery messages; semantic category colors in light/dark themes.
- [x] Shared vector logo, favicon and packaged macOS icon, with a reproducible asset build.
- [x] Synthetic two-account browser checks in four widths and both themes; real HTTP and persisted-state checks.
- [x] Existing authorized Gmail read-only runtime: 100 inbox messages loaded, ready, no model error.

## Real-account acceptance still requires those accounts' authorization

- [ ] Connect the user's remaining Gmail / Microsoft / IMAP accounts through the workbench.
  No client credential extraction; each grant must be completed by the mailbox owner.
- [ ] Verify two real accounts simultaneously, new-mail arrival, token expiry and reauthorization;
  synthetic adapter tests do not prove real provider/tenant behavior.
- [ ] Verify IMAP OS vault enrollment on the user's chosen provider and Microsoft tenant device flow.
- [ ] Dedicated authorized send/reply/attachment/trash/restore round trips on each provider; fixture tests do not establish live provider acceptance.
- [ ] Dedicated test-mailbox round trip: classify → correction in Gmail / Outlook / Apple Mail →
  server label state → existing CLI learning. Read-only native visibility was checked; mutation was not.

## Remaining product work

- [ ] Unified local corrections → optional provider write-back and consented learning; separate from completion.
- [ ] Incremental Graph/IMAP sync, custom-folder scope, and broader history beyond the bounded per-folder window.
- [ ] Account disconnect/revocation/removal policy, without erasing other accounts or silently discarding workflow state.
- [ ] Multi-account resource measurements and consented Chinese-language quality evaluation; historical one-mailbox
  model results do not prove new-user recall or false-positive rates.
- [ ] Public distribution: OAuth verification/public client strategy, Windows code signing (macOS releases are
  signed and notarized: docs/releasing.md), update/rollback/uninstall.
  Neither the local launcher nor the app is a complete replacement for an email client yet.
- [x] Desktop app: macOS, Windows and Linux package builds plus window/TLS/start/quit smokes passed in
  [Desktop app run 43182995849](https://github.com/scamai/email-town-labeler/actions/runs/43182995849).
- [ ] Intel Mac build; GUI checks of attachment save, file picker and external links in the native window.
  The owner's updated signed installation still needs unlocked signing credentials and native acceptance;
  see [the daily-mail review](docs/daily-mail-review.md).

Usage: [本机工作台](docs/desktop-workbench.md) · [English guide](docs/desktop-workbench.en.md).
Existing-client acceptance protocol: [client workflow](docs/client-workflow.md).
