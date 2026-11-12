# Daily-mail readiness review

Started 2026-11-11 from `e178fd0`. The acceptance goal is a dependable daily workflow:
connect accounts → find pending/unread mail → read/reply → finish → recover or reopen.
This review uses synthetic messages and authenticated local servers. It does not use real
mail as fixtures or transmit real mail to coding agents.

## Confirmed defects and repair status

| Priority | Finding | Reproduction / consequence | Acceptance |
| --- | --- | --- | --- |
| P1 | Done has no useful default destination | Inbox defaults to completion=all; Done leaves the row with a strike-through | Pending Inbox removes it; Completed finds it; Reopen restores it; category survives |
| P1 | Routine navigation recreates every list row | In a 40-message fixture, 20 Down presses remove 800 row nodes | Unchanged rows retain identity, focus, geometry and scroll; changed rows update without whole-list teardown |
| P1 | Queued flags overwrite each other | On an unread message, queue read then star while provider writes are blocked: snapshot becomes unread=true/starred=true | Independent fields retain their queued state; older requests cannot remove newer overlays |
| P1 | Automatic read overtakes later explicit unread | Separate queues can write unread then stale read, leaving provider and UI read | PR #55 shares one ordered outbox; final intent and immediate failure rollback verified |
| P1 | Unread work is unclear in sidebar sections | Category stats show total/done/“wrong”, not unread | Explicit unread counts with documented account/folder/filter scope |
| P1 | “0 wrong” suggests unsupported accuracy | Counter only observes manual category changes; there is no reviewed denominator | Say corrected/manual/automatic/pending as appropriate; do not imply unreviewed predictions are correct |

The queued-flag fix landed in [PR #52](https://github.com/scamai/email-town-labeler/pull/52).
Seven new regression cases failed before the fix and passed afterward; the full suite passed
787 tests with nine absent optional-runtime skips. The lead also verified a fresh wheel
installation, distribution contents and a non-root Docker smoke run.

The stable-list fix landed in [PR #53](https://github.com/scamai/email-town-labeler/pull/53).
The original 40-message/20-Down reproduction now removes zero row nodes, versus 800 before.
WebKit and Chromium verify row/control identity, selection, current-state row actions, reordered-row
focus and visibility, background refresh, held responses, Done/undo, and narrow Back/Escape.
The lead found and fixed an additional key-only focus-restoration edge during independent review.
Old assets fail the identity regression, and the pre-guard version fails the Escape regression.
Hover/focus geometry passed at 1280, 1000 and 390 px in both engines; 787 unit tests passed with
nine optional-runtime skips, and all build/distribution/Docker checks passed.

The daily-workflow changes landed in [PR #54](https://github.com/scamai/email-town-labeler/pull/54):
Pending/Completed/Reopen, composed filters, scoped unread counts, factual classification statistics and
useful empty states. Two synthetic Gmail identities verify reply From selection and account isolation.
Failed or paused accounts are distinguished from empty folders. The narrow unread badge previously
changed header height from 50.75 to 41 px in WebKit (50.5 to 41 in Chromium); both now stay at 41 px.
The regression failed before this fix. Final checks passed 787 tests with nine optional-runtime skips,
16 viewport/theme combinations across two engines, compose/send journeys, keyboard/navigation,
completion, reconciliation, category statistics, layout and hover/focus geometry. Installed wheel UI
assets match the delivered source; wheel/sdist/Docker checks passed.

The final cross-queue repair landed in [PR #55](https://github.com/scamai/email-town-labeler/pull/55).
A deterministic barrier test reproduced provider writes of unread then stale read, leaving the message
read after refresh. Automatic opening and explicit flags now share the ordered outbox. Pending flags are
snapshot/detail overlays rather than stored mailbox state; a regression caught and prevented rollback
poisoning during implementation review. Other checks cover repeated opens, read/unread/read, all failure
combinations, existing epoch rejection of delayed lists, and external read-state changes after completion.
The final candidate passed 797 tests with nine optional-runtime skips, Ruff, distributions, fresh installed
wheel CLI and non-root Docker checks. Both browser engines passed navigation, reconciliation, completion,
row actions, selection and shortcuts. Reconciliation fixtures now synchronize with automatic-read
refreshes and restore test reordering by captured IDs. A deliberate whole-list-rebuild mutation of the
current assets fails the live row/control identity assertion; assertions still detect the original defect.

Changes are prepared by Kimi K3.1 Pro with max reasoning in isolated worktrees. The lead
reviews diffs, reruns evidence and handles publication. No coding agent is allowed to send
real mail as part of this review.

## Baseline evidence

- 106 backend checks passed: HTTP authentication/boundaries, enrollment, multi-account loading,
  provider adapters, workflow state, cache, images, send receipts and mail-operation failures.
- 61 classification/policy/learning checks passed. These test implementation contracts and
  synthetic learning behavior, not the accuracy of a deployed model on a user's mailbox.
- 206 model package/worker, service and update checks passed. Nine optional model-runtime
  checks skipped because sentence-transformers, torch, transformers or onnxruntime are absent.
- `check_mail_ui.py` passed composition, saved drafts, attachments, reply-all/forward setup,
  uncertain-send recovery and Trash/Restore against synthetic mail.
- `check_desktop_ui.py` passed eight viewport/theme combinations, HTTP actions, undo, local
  category persistence, reload, keyboard and authentication.
- `check_layout_stability_ui.py --report` passed hover/focus geometry checks at 1280, 1000
  and 390 px in WebKit and Chromium. This does not test DOM identity during state updates;
  the 800-row-removal navigation reproduction exposes a gap the older test did not cover.
- The earlier arrow navigation regression passed Chromium and WebKit; it fixed detached-node
  focus/scroll use but did not eliminate whole-list rendering.

The final evidence and remaining acceptance boundaries are maintained in [VERIFY.md](../VERIFY.md).

## Security review

The main attacker-controlled inputs are incoming MIME/headers/attachments/image references,
browser requests to the local server, provider API responses and optional model packages.
The local OS account and its explicitly enrolled mailbox grants are trusted boundaries.

Existing controls were traced and exercised:

- The server binds numeric loopback, checks Host and Origin, uses bearer session authorization,
  and requires an application header for writes. Session credentials are not shared cookies.
- Email HTML/scripts are not rendered. Plaintext and bounded image descriptors are used;
  external image loading requires a click for the current preview. Inline images require a
  referenced raster attachment with a validated MIME signature and size limit.
- MIME header inputs are validated, Bcc stays out of transmitted message headers, attachments
  have normalized filenames, and reply requests must use the receiving account.
- Durable operation receipts reject changed payloads under an existing request ID. Unknown
  send results retain drafts and do not automatically resend. A newer draft edit is not removed
  when an earlier version finishes sending.
- IMAP uses TLS and UID-scoped mutations; the app does not broadly expunge a mailbox. Microsoft
  API requests use immutable IDs on reads and writes, avoiding the ordinary move-ID change.
- The Linux baseline dependency audit reported no known vulnerabilities in 36 audited dependencies.
  A second audit covered all 63 third-party packages in the actual macOS build environment, including
  its app/model/build dependencies: no known vulnerabilities and no skipped packages. These exact
  installed versions were exported as pinned requirements and checked with `pip-audit --no-deps
  --disable-pip --strict`; the local project package was excluded. This does not audit native system
  libraries or optional runtimes outside those environments.
- Tracked credential-filename/history inspection found no committed token/client credential JSON,
  `.env` or SQLite paths. Gitleaks 8.30.1 additionally scanned all 76 reachable commits (2.10 MB) in
  the non-shallow repository with default rules, an empty ignore file, allow-comments disabled and
  redacted output: no leaks found. The tool's release checksum was verified. This is a bounded scanner
  result, not proof that no secret exists under an unrecognized format.
- Privacy documentation had stale preview-cache limits. It now matches the code: 300 server
  previews, 150 details per open page, 30 Gmail/10 Microsoft or IMAP bodies read ahead per
  account, and a server body-text limit of 262,144 characters (not bytes).

No exploitable security vulnerability is confirmed by these checks. This is not a penetration
test or a guarantee of absence; browser rendering and native OS integration have distinct
acceptance requirements.

## What “other mailboxes work” can currently mean

Gmail, Microsoft Graph and TLS IMAP are wired to account-bound adapters. Multiple enabled
accounts refresh independently with at most three account workers, and a failed account does
not erase another account's loaded mail. Synthetic tests verify identity isolation, enrollment
checks, pagination, expired grants, read/write capabilities and partial operation failure.

The Linux review host has no configured real account registry or running real workbench. The owner
selected a **second Gmail**, confirmed that it is not yet added or authorized, and uses the installed
macOS app. An accessible Mac has the company signing identity, but its login keychain is locked.
No live second-account loading or mail delivery is established by synthetic tests. Remaining acceptance
requires owner enrollment through the app, then:

1. Load two real accounts together; check new-mail arrival, refresh and one-account failure.
2. Check expired credentials, reconnect and duplicate-account rejection.
3. Use an expressly authorized test mailbox for reply/reply-all, send, attachments and partial
   failure recovery; verify the recipient and Sent copy, not merely a success toast.
4. Check IMAP special-use folder discovery, UID MOVE support, SMTP settings and OS vault access
   on the actual chosen server and desktop OS.

Done is local completion, not provider Archive or Trash. Classification tags and completion
are independent. Local overrides are keyed by account and provider message identity; do not
infer that they survive an IMAP server changing UID/UIDVALIDITY or moving between folders.

## Classification limitations

Provider categories take precedence where available. Otherwise the desktop runs a local
classifier and applies the policy thresholds. Sender familiarity is intentionally not shared
between accounts. Manual corrections persist as local overrides with undo; they do **not**
currently feed the CLI learning pipeline or write back to provider labels.

There is no representative, consented, multi-account labeled evaluation set in this review.
Accuracy, missed action-needed messages and confidence calibration therefore remain unmeasured.
Counts of changed categories cannot substitute for those measurements. A later quality gate
needs reviewed examples, per-class confusion/recall and false-positive analysis, and a held-out
set before claiming the model can reliably prioritize the owner's daily mail.

## Product direction

Borrow the separation of active work and Completed from
[Apple Reminders](https://support.apple.com/guide/reminders/view-reminder-lists-remnd854fc47/mac),
while keeping mailbox folders, account identity and category tags visible. Filters should
compose and report their scope. High-frequency operations should update state without
animation or list teardown; all essential controls must work with keyboard and narrow layouts.

The browser content is exercised in WebKit and Chromium on Linux. The [three-platform desktop run](https://github.com/scamai/email-town-labeler/actions/runs/43182995849)
passed native package builds and window/TLS smokes on macOS, Windows and Linux at source
`426f6d7`. A separate arm64 build on an accessible Mac passed the bundled self-check (9/9), model
worker, HTTPS, real Cocoa window, single-instance reuse, authenticated shutdown and child-process cleanup
with an isolated temporary home. Three tiny ONNX inference tests that lacked runtimes on Linux also
passed on the Mac (two classifier pooling cases and one sequence graph); they exercise implementation,
not real-mail classification accuracy. Test-only dependencies were added after the app binary was built.
The workflow's release-attachment job is intentionally skipped for
manual runs; unsigned CI macOS artifacts are not official release installers.

Signed/notarized installation on the owner's Mac remains blocked by its locked login keychain;
`notarytool` returned `keychainLocked`. The new build is prepared, but the installed app has not been
replaced. The remote test process also reports `AXIsProcessTrusted() == false`, so native keyboard
acceptance needs an Accessibility-authorized execution context. A later remote-access preflight was
also refused by the local signing agent; deployment needs that access restored. The [release procedure](releasing.md) requires the company identity, Apple notarization,
and installed-app Accessibility verification. File pickers, real OAuth return paths and second-account
mail round trips remain separate gates. These results do not yet establish a complete replacement for
the owner's existing mail client.

## Visual-lint disposition

The final source design scan reported no FAIL, with five warning categories. They are
lower priority than the reproduced workflow/state defects:

- Spacing: four off-grid values in the final scan. The new 6 px horizontal folder-tab padding
  keeps names and unread badges on one stable row in narrow windows without reducing type size.
  The other values are badge padding, an optical negative margin and category metadata spacing;
  they did not cause the rendered hover/focus geometry failures.
- Type: nine declared sizes versus the generic six-size floor. This includes compact reader
  metadata and avatar/control roles; do not flatten these roles merely to satisfy a scanner.
- Radius: five sizes versus four; a token consolidation is a P2 follow-up, not a blocked mail task.
- Shadows: the scan counts inset active-row/category indicators as shadows. Those communicate
  state rather than floating elevation, so the generic three-shadow threshold is not applicable.
- Color: sender avatars use dynamic HSL custom properties by design. The star's fixed gold
  color remains a P2 token-consistency follow-up owned by the UI maintainer.

Static scanning cannot establish alignment, text contrast, focus order or complete recovery
flows. Those require the rendered and interaction checks in the acceptance matrix.

## Recovery follow-ups

Read-only tracing identified two P2 recovery improvements to validate separately from list
stability: the initial state-fetch error offers a full page restart rather than an in-place
retry, and a preview that times out during cached-account startup has no explicit retry button.
After initialization, state polling retries after three seconds and retains prior mail; a
browser regression should assert that the stale indicator clears after service recovery.
Per-account errors are isolated and sanitized, but reconnect needs real-provider acceptance.
