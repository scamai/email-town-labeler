# Daily mail acceptance

Review started 2026-11-11 against `e178fd0`. Dominant surface: local desktop mail client
(Python loopback server and shared browser UI); CLI/model backends are also in scope.
Synthetic fixtures never establish acceptance by a real mailbox provider.

Statuses: ✅ exercised; ❌ reproduced defect; ⬜ pending review; ⚠ external acceptance required.

| ID | Checkpoint | Status | Evidence / remaining work |
| --- | --- | --- | --- |
| MAIL01 | Opening, keyboard navigation, selection, read/star state | ✅ | `check_shortcuts_ui.py`, `check_list_navigation_ui.py` in Chromium and WebKit |
| MAIL02 | Done leaves pending work; Completed can reopen; tags persist | ❌ | Default `filters.done = all` leaves completed rows struck through in Inbox; workflow redesign in progress |
| MAIL03 | Account/category/date/completion/search filters compose and explain scope | ⬜ | Audit all filter combinations and clear-filter behavior |
| MAIL04 | Section/account/category unread counts are explicit and scoped | ❌ | Existing category counts contain total/done/corrections, no unread count |
| MAIL05 | No list replacement, focus loss or scroll jump on routine updates | ❌ | `renderList()` replaces all children on each open/update; stable rendering work in progress |
| MAIL06 | Concurrent queued flags preserve latest intent across different fields | ✅ | PR #52: seven regressions fail before fix and pass after; latest per-field intent survives refresh and earlier completion/failure |
| MAIL07 | Reply, reply-all, forward, drafts, attachments, send and failure recovery | ✅ | `check_mail_ui.py`; synthetic server/provider tests. Live delivery remains MAIL15 |
| MAIL08 | Duplicate/uncertain send is not automatically retried; newer drafts survive | ✅ | `tests/test_desktop_mail.py`: durable receipt, timeout, partial send, atomic draft cleanup |
| MAIL09 | Bulk partial failure, safe Trash/Restore and cross-account isolation | ✅ | `tests/test_desktop_mail.py`, including no broad IMAP expunge and no cross-account reply |
| MAIL10 | Multi-account refresh and failure isolation | ✅ | `tests/test_desktop_manager.py`, `test_desktop_enrollment.py`, `test_desktop_providers.py`; synthetic only |
| MAIL11 | Classification source, uncertainty and corrections are represented honestly | ❌ | “0 wrong” counts only observed manual corrections, not measured accuracy |
| MAIL12 | Manual category/completion persist independently through reload | ✅ | `check_desktop_ui.py`: real HTTP actions, undo, local category, reload |
| MAIL13 | Desktop/narrow/phone, light/dark, keyboard and accessible controls | ⬜ | Baseline desktop check passes 8 viewport/theme combinations; post-fix audit pending |
| MAIL14 | Empty, loading, offline, stale and account-error recovery | ⬜ | Exercise explicit recovery paths alongside healthy-account isolation |
| MAIL15 | Real second Gmail, Microsoft and IMAP account load and mail round trips | ⚠ | Owner prioritizes second Gmail. No account registry or live workbench on review host; requires owner enrollment and test-mailbox round trips |
| MAIL16 | Classification quality on representative labeled mail | ⚠ | No consented labeled multi-account evaluation set; correction counts cannot establish accuracy |
| SEC01 | Host/origin/session protections and private state | ✅ | `test_desktop_server.py`, `test_desktop_mail.py`; plaintext receiver input never becomes executable HTML |
| SEC02 | Email images, downloads, header injection and Bcc privacy | ✅ | `test_desktop_images.py`, `test_desktop_mail.py`; remote images require explicit per-preview load |
| SEC03 | Dependencies and tracked secret-history inspection | ⚠ | `pip-audit` found no known vulnerabilities among 36 audited installed dependencies; local package/absent optional extras excluded; tracked credential-path/history checks found none, dedicated secret scanner unavailable |
| SHIP01 | Lint, full tests, distributions, Docker and installed-wheel smoke | ⬜ | Run against final delivered changes; optional model dependencies and native platforms reported separately |
| SHIP02 | Native packaged app and real provider end-to-end acceptance | ⚠ | Browser tests on Linux do not validate macOS/Windows shell, OAuth/vault, native file picker or delivery |

## Review boundaries

No public website SEO audit applies to the authenticated localhost UI. Repository marketing
metadata is unrelated to this daily-mail review. No real email or credentials are included in
fixtures, captures, logs, or this checklist. Passing automated checks is not a claim that every
classification is correct, that every provider works live, or that all security flaws are absent.
