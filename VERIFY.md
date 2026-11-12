# Daily mail acceptance

Review started 2026-11-11 against `e178fd0`. Dominant surface: local desktop mail client
(Python loopback server and shared browser UI); CLI/model backends are also in scope.
Synthetic fixtures never establish acceptance by a real mailbox provider.

Statuses: ✅ exercised; ❌ reproduced defect; ⬜ pending review; 🟡 partial / follow-up; ⚠ unavailable or external acceptance required.

| ID | Checkpoint | Status | Evidence / remaining work |
| --- | --- | --- | --- |
| MAIL01 | Opening, keyboard navigation, selection, read/star state | ✅ | `check_shortcuts_ui.py`, `check_list_navigation_ui.py` in Chromium and WebKit |
| MAIL02 | Done leaves pending work; Completed can reopen; tags persist | ✅ | PR #54: two-Gmail fixture verifies Pending/Completed/Reopen, all-done recovery, unchanged provider folder/labels and retained category tags |
| MAIL03 | Account/category/date/completion/search filters compose and explain scope | ✅ | `check_completion_ui.py`: combined scopes, readable/accessible context and reset to Pending in both engines |
| MAIL04 | Section/account/category unread counts are explicit and scoped | ✅ | PR #54: explicit unread counters follow other filters; only loaded active-folder count shown; last unread badge no longer moves narrow list |
| MAIL05 | No list replacement, focus loss or scroll jump on routine updates | ✅ | PR #53: same 40-row/20-Down fixture removes zero rows instead of 800; WebKit/Chromium identity, reorder focus/visibility, background updates, narrow Back/Escape and geometry pass |
| MAIL06 | Concurrent queued flags preserve latest intent across different fields | ✅ | PR #52: seven regressions fail before fix and pass after; latest per-field intent survives refresh and earlier completion/failure |
| MAIL07 | Reply, reply-all, forward, drafts, attachments, send and failure recovery | ✅ | `check_mail_ui.py`; synthetic server/provider tests. Live delivery remains MAIL15 |
| MAIL08 | Duplicate/uncertain send is not automatically retried; newer drafts survive | ✅ | `tests/test_desktop_mail.py`: durable receipt, timeout, partial send, atomic draft cleanup |
| MAIL09 | Bulk partial failure, safe Trash/Restore and cross-account isolation | ✅ | `tests/test_desktop_mail.py`, including no broad IMAP expunge and no cross-account reply |
| MAIL10 | Multi-account refresh and failure isolation | ✅ | `tests/test_desktop_manager.py`, `test_desktop_enrollment.py`, `test_desktop_providers.py`; synthetic only |
| MAIL11 | Classification source, uncertainty and corrections are represented honestly | ✅ | PR #54 replaces “wrong” with automatic/corrected/awaiting facts; category-source labels persist. Accuracy remains MAIL16 |
| MAIL12 | Manual category/completion persist independently through reload | ✅ | `check_desktop_ui.py`: real HTTP actions, undo, local category, reload |
| MAIL13 | Desktop/narrow/phone, light/dark, keyboard and accessible controls | ✅ | Both engines pass 16 viewport/theme combinations total, keyboard journeys and layout/geometry checks; physical devices and screen readers remain SHIP02 |
| MAIL14 | Empty, loading, offline, stale and account-error recovery | 🟡 | Empty Pending, configured empty folders, failed/paused second account and healthy-account retention pass; explicit retry affordances and stale-recovery browser regression remain P2 follow-ups |
| MAIL15 | Real second Gmail, Microsoft and IMAP account load and mail round trips | ⚠ | Owner prioritizes second Gmail. No account registry or live workbench on review host; requires owner enrollment and test-mailbox round trips |
| MAIL16 | Classification quality on representative labeled mail | ⚠ | No consented labeled multi-account evaluation set; correction counts cannot establish accuracy |
| MAIL17 | Automatic open-to-read and explicit unread preserve cross-queue intent | ❌ | Deterministic barrier reproduction sends unread then stale read, ending remotely read after refresh; separate backend fix in progress |
| SEC01 | Host/origin/session protections and private state | ✅ | `test_desktop_server.py`, `test_desktop_mail.py`; plaintext receiver input never becomes executable HTML |
| SEC02 | Email images, downloads, header injection and Bcc privacy | ✅ | `test_desktop_images.py`, `test_desktop_mail.py`; remote images require explicit per-preview load |
| SEC03 | Dependencies and tracked secret-history inspection | ⚠ | `pip-audit` found no known vulnerabilities among 36 audited installed dependencies; local package/absent optional extras excluded; tracked credential-path/history checks found none, dedicated secret scanner unavailable |
| SHIP01 | Lint, full tests, distributions, Docker and installed-wheel smoke | ✅ | PR #54 final candidate: 787 passed, 9 absent optional ML-runtime skips; Ruff, builds, installed UI asset comparison, sdist inspection and non-root Docker smoke pass. MAIL17 follow-up is not yet delivered |
| SHIP02 | Native packaged app and real provider end-to-end acceptance | ⚠ | Browser tests on Linux do not validate macOS/Windows shell, OAuth/vault, native file picker or delivery |

## Review boundaries

No public website SEO audit applies to the authenticated localhost UI. Repository marketing
metadata is unrelated to this daily-mail review. No real email or credentials are included in
fixtures, captures, logs, or this checklist. Passing automated checks is not a claim that every
classification is correct, that every provider works live, or that all security flaws are absent.
