# Security policy

email-town-labeler holds an OAuth token to your mailbox, so we treat security and privacy bugs with
priority. It is maintained by Reality Inc. (Scam.ai). For this repository this page replaces
[Reality Inc.'s organization-wide policy](https://github.com/scamai/.github/blob/main/SECURITY.md)
and keeps that policy's response targets and safe harbor.

## Supported versions

| Version | Supported |
|---|---|
| The [latest release](https://github.com/scamai/email-town-labeler/releases/latest) | Yes |
| Any earlier release | No |

email-town-labeler is pre-1.0. Fixes ship only in a new release. Please check that you run the
latest one (`email-town-labeler --version`) before you report.

## Reporting a vulnerability

**Please do not report security vulnerabilities in public issues, discussions or pull
requests.** Report them privately, in one of two ways:

- **GitHub private vulnerability reporting (preferred).** Use **Report a vulnerability** on this
  repository's Security tab, or go straight to
  <https://github.com/scamai/email-town-labeler/security/advisories/new>.
- **Email.** Write to contact@scam.ai with the subject line `[SECURITY][Email Town]`.

Please include:

- what the problem is, what an attacker could do with it, and what they need (local access, a
  crafted email, a malicious model package, ...);
- how to reproduce it, using **synthetic** mail only. Never send us real email content,
  `token.json`, `client_secret.json` or any other token or secret: we never look at the content
  of your mail, and a report does not need it;
- your email-town-labeler version (`email-town-labeler --version`), how you installed it (pipx,
  pip, uv, Docker or from source), your OS and your Python version;
- how we can reach you.

## In scope

Anything that breaks the guarantees in [docs/privacy.md](docs/privacy.md), for example:

- mail content (subject, sender, addresses, snippet, body) written to logs, the database,
  exceptions or `status` / `doctor` output, or sent anywhere other than Google and, if you
  chose the `ollama_choice` backend, your Ollama server;
- `ollama_choice` sending mail text to a host other than this machine when you have not set
  `allow_remote_endpoint`, or a model package that can set it for you;
- the update check sending more than [docs/privacy.md](docs/privacy.md#update-check) lists, or
  running from `run`, the Docker image's default command or its health check;
- email-town-labeler modifying labels outside its own prefix, or archiving, deleting, sending or
  marking mail read;
- token or client-secret exposure (wrong file permissions, leaking into logs or images);
- a model package that can execute code, escape its directory on install, or bypass sha256
  verification;
- network calls not described in the docs.

## Out of scope

- Ordinary bugs, wrong labels and feature requests. These go to the
  [public issue tracker](https://github.com/scamai/email-town-labeler/issues), without real
  mail (see the bug report form).
- The breadth of the `gmail.modify` scope itself, which
  [docs/privacy.md](docs/privacy.md#what-gmailmodify-allows-and-what-email-town-labeler-does)
  explains. email-town-labeler using it for anything beyond reading mail and changing its own
  labels is in scope.
- Vulnerabilities in Gmail, Google's OAuth, Ollama, Python or a dependency itself. Please
  report those to their own projects; tell us too if email-town-labeler should work around one.
- Denial-of-service testing against Reality Inc.'s online services, social engineering of
  Reality Inc. staff, physical attacks, and third-party services we do not control.

## What to expect

- **Acknowledgment within 2 business days, and an initial assessment within 5 business days.**
  These are the same targets as in
  [Reality Inc.'s security policy](https://github.com/scamai/.github/blob/main/SECURITY.md).
- We will keep you informed while we work on a fix or mitigation, and coordinate disclosure
  with you.
- We credit reporters in the release notes of the fix and in any advisory, unless you prefer to
  stay anonymous.
- There is no bug bounty.

## Safe harbor

We will not pursue legal action against researchers who make a good-faith effort to follow this
policy, avoid privacy violations and service disruption, and allow us reasonable time to
remediate before public disclosure.

This is the safe harbor from
[Reality Inc.'s security policy](https://github.com/scamai/.github/blob/main/SECURITY.md#safe-harbor),
applied to email-town-labeler.

## Getting fixes

From 0.1.1 on, `setup`, `status`, `doctor`, `model list` and `model pull` tell you, at most once a
day, when a newer release exists, and print the command that installs it, unless you turned the
update check off ([docs/privacy.md](docs/privacy.md#update-check)). To hear about every release,
including security fixes, choose **Watch → Custom → Releases** on this repository, or subscribe
to <https://github.com/scamai/email-town-labeler/releases.atom>.

## If your token may be exposed

1. Revoke access at <https://myaccount.google.com/permissions>.
2. Delete `token.json` (see [docs/configuration.md](docs/configuration.md#files-and-directories)).
3. Optionally delete and recreate the OAuth client in your Google Cloud project.
4. `email-town-labeler login` to issue a new token.
