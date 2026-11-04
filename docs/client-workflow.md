English · [中文评估](research/2026-10-30-client-integration.html)

[← README](../README.md)

# Daily mail in Gmail, Outlook and Apple Mail

Contents: [Check visibility](#check-visibility) · [Daily searches](#daily-searches) ·
[Client setup](#client-setup) · [Multiple accounts](#multiple-accounts) ·
[Acceptance checks](#acceptance-checks) · [Limits](#limits)

These commands are in the source checkout's **Unreleased** version. They do not imply that
an older installed package or the v0.1.0 download already contains them.

## Check visibility

```bash
email-town-labeler labels check
email-town-labeler labels check --json
email-town-labeler labels show --dry-run
```

`check` reads each expected label's visibility from Gmail. It exits 1 if a label is missing,
hidden, only visible when unread, or has unknown visibility; API failures also fail the command.
It does not load the model, read messages, or alter classification state. A missing parent is
reported even though Gmail can display nested children without one.

If the planned changes match what you want, stop this account's labeler and run:

```bash
email-town-labeler labels show
```

This explicitly creates missing configured class labels and the parent, then makes them visible
in Gmail's sidebar and message list. Existing colors, names, message labels, read state and
unrelated labels are preserved. Startup does **not** automatically undo deliberate hiding.
The write command respects `[engine] dry_run` and takes the same per-account lock as `run`;
stop the matching service first and start it again afterward. `check` and dry-run are read-only
and can run while the engine is polling.

This checks Gmail web visibility, **not** IMAP subscriptions, Outlook favorites, Apple Mail
sidebar settings, or whether a client has finished syncing. Verify those in the actual client.
See the [Gmail label resource](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.labels).

## Daily searches

```bash
email-town-labeler triage --plan                  # offline, copyable Gmail queries
email-town-labeler triage                        # bounded live message counts
email-town-labeler triage --view backlog --limit 500
email-town-labeler triage --date 2026-10-30 --timezone America/Los_Angeles --json
```

| View | Includes |
|---|---|
| `action` | Inbox messages with the configured action-needed label, all dates |
| `today` | Action-needed inbox messages received on the selected calendar day |
| `backlog` | Action-needed inbox messages received before that day |
| `new-sender` | Inbox messages with the configured new-sender label, all dates |
| `unclassified` | Inbox messages with none of the configured class labels, all dates |

Today is resolved in the OS local timezone unless `--timezone` supplies an IANA zone.
The query uses epoch seconds so Gmail's PST interpretation of date strings cannot shift the
calendar boundary. Named zones require the OS timezone database or the optional `tzdata`
package, particularly on Windows. Spring/fall DST days may be 23/25 hours.

`--plan` works without login, model weights, or a state database. Its date-specific queries
are a **snapshot**, not rolling bookmarks: regenerate them each day. All-date `action`,
`new-sender` and `unclassified` queries can be reused. Custom label names are respected;
quotes, backslashes and control characters are rejected rather than generating ambiguous searches.

Live counts come from Gmail search, not historical classification decisions. The default limit
is 100 per view (allowed range 1–1000). `100+` / JSON `truncated: true` means more matches exist;
the tool fetches one extra ID to distinguish that case from exactly 100. No subjects, addresses,
message IDs or bodies are printed by `triage`. No message body/metadata request is made.

The views overlap and their counts must not be summed. They are separate live requests, not a
transactional snapshot. Gmail web conversation results may contain additional messages; API
counts are message-level. “Unclassified” includes intentionally skipped mail and old mail never
backfilled, not only failed or pending classifications. The new-sender view can still find older
labels even if that rule is now disabled.

[Google search semantics](https://developers.google.com/workspace/gmail/api/guides/filtering).

## Client setup

First confirm the **mailbox provider**. A Gmail account in Outlook remains a Gmail account;
it continues to use this project's Gmail connector. A Microsoft 365 mailbox needs a different
connector, which is not implemented yet.

- **Gmail:** keep the project's label group visible; copy a generated query into search in the
  matching account. Do not assume the currently selected browser account is the one you configured.
- **Outlook for Mac:** expand the Gmail account and the configured prefix (default `e`). Open
  `action needed`; keep a convenient folder shortcut where your client version supports it.
  Outlook saved searches use Outlook's own criteria, not the generated Gmail query syntax.
- **Apple Mail:** expand the account and `e`, then open the classification mailbox. A Smart Mailbox
  can use account, mailbox and date criteria; check its scope against the original folder before
  using it as your daily entry point. Do not combine criteria in a way that hides all older mail.

In one live check on 2026-10-30, the same Gmail account's seven categories appeared in Gmail web,
Outlook for Mac 16.114.3 and Apple Mail 16.0, and the action-needed list opened in both desktop
clients. Other versions, providers and write operations were not established by that check.

**Completion is separate from classification.** The engine treats class-label removal/replacement
as learning feedback. Do not remove `action needed` merely to say “finished.” Reading does not
mean finished either. The tool does not add a completion label or archive mail. If you already
use manual archiving as completion, an archived message leaves these inbox-only searches while
retaining its classification; all-date classification folders can still contain archived mail.

## Multiple accounts

Use a distinct data home **and a distinct service name** for each account. No account registry
or combined model process exists yet. Model packages and loaded workers remain per account;
several large models may consume substantial memory.

```bash
email-town-labeler --home /absolute/path/account-work setup --client-secret /path/client.json
email-town-labeler --home /absolute/path/account-work service install --name work
email-town-labeler --home /absolute/path/account-work service status --name work
email-town-labeler --home /absolute/path/account-work status --service-name work --redact
email-town-labeler --home /absolute/path/account-work doctor --service-name work --redact
email-town-labeler --home /absolute/path/account-work triage

email-town-labeler --home /absolute/path/account-personal setup --client-secret /path/client.json
email-town-labeler --home /absolute/path/account-personal service install --name personal
```

For a single setup command, `setup --install-service --service-name work` selects the same named
service. Names are 1–40 ASCII characters, start with a lowercase letter, and contain only lowercase
letters, digits or hyphens. Use the same name for install, status and uninstall. Names are global
within your OS user account: reusing a name can replace that named service even with another home.

The original unnamed service is unchanged for compatibility. An existing account using it does
not need migration: leave it alone and give the second account a new name. To deliberately move
an existing account from unnamed to named, uninstall its old service first. Do not install two
services pointing at the same home; the instance lock will prevent concurrent engines but the
extra service can keep trying to restart. `status`/`doctor` without `--service-name` probe the
original unnamed service.

```bash
email-town-labeler --home /absolute/path/account-work service uninstall --name work
```

This stops/removes only that service definition; it does not delete account data or Gmail labels.

## Acceptance checks

Use a dedicated test account and synthetic mail. No real-mail write test is claimed by the
in-memory suite. Record account/provider, client/version, result and any delay for each check.

1. Classify test mail; check Gmail and each configured desktop client for the same category.
2. Include old action-needed mail, today’s mail, read-but-unfinished mail, unclassified mail and
   archived mail. Verify daily views against the underlying inbox and account.
3. Change a test message's classification in the client; verify the exact final label set in
   Gmail, then confirm one appropriate correction enters learning. Test copy and move separately;
   client folder gestures are not necessarily equivalent to replacing one classification label.
4. Exercise the chosen manual completion convention and verify it does not create a classification
   correction or silently remove an unfinished item. A new “Done” mechanism requires its own design.
5. Disconnect/reconnect the client and retry; check no duplicates or wrong-account actions.
6. With two separately configured accounts and service names, stop one service and verify the other
   keeps polling. OS command-generation tests do not prove a real scheduler or OAuth setup works.

## Limits

There is no Microsoft Graph connector, generic IMAP connector, Outlook add-in, MailKit extension,
GUI account manager, completion-state sync or signed desktop installer in this change. No new
permission scopes are requested. The implementation adds tested building blocks and client setup
instructions; it does not claim universal integration or that a new app has shipped.

For implementation priorities and outstanding acceptance work, see [TODO.md](../TODO.md).
