# Security policy

email-town-labeler holds an OAuth token to your mailbox, so we treat security and privacy bugs with
priority.

## Reporting a vulnerability

**Please report privately to security@scam.ai.** Do not open a public issue, pull request
or discussion for a vulnerability.

Include:

- the email-town-labeler version (`email-town-labeler --version`), OS and Python version;
- what an attacker can do and what they need (local access, a crafted email, a malicious model
  package, ...);
- steps to reproduce, using **synthetic** data only. Never send us real email content,
  tokens or client secrets.

You can also use GitHub's private vulnerability reporting ("Report a vulnerability" on the
repository's Security tab) if it is enabled.

What to expect:

- acknowledgement within 3 business days;
- an initial assessment within 10 business days;
- a fix or mitigation plan, coordinated disclosure, and credit in the release notes if you
  want it.

## What counts

Anything that breaks the guarantees in [docs/privacy.md](docs/privacy.md), for example:

- mail content (subject, sender, addresses, snippet, body) written to logs, the database,
  exceptions, `status` / `doctor` output, or sent anywhere other than Google;
- email-town-labeler modifying labels outside its own prefix, or archiving, deleting, sending or
  marking mail read;
- token or client-secret exposure (wrong file permissions, leaking into logs or images);
- a model package that can execute code, escape its directory on install, or bypass sha256
  verification;
- network calls not described in the docs.

Ordinary bugs, wrong labels and feature requests go to the public issue tracker.

## Supported versions

email-town-labeler is pre-1.0. Security fixes go into the latest minor release only.

| Version | Supported |
|---|---|
| 0.1.x (latest) | ✓ |
| < 0.1 | no |

## If your token may be exposed

1. Revoke access at <https://myaccount.google.com/permissions>.
2. Delete `token.json` (see [docs/configuration.md](docs/configuration.md#files-and-directories)).
3. Optionally delete and recreate the OAuth client in your Google Cloud project.
4. `email-town-labeler login` to issue a new token.
