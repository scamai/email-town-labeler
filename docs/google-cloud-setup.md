# Google Cloud setup

email-town-labeler talks to Gmail through the official Gmail API with **your own** OAuth client.
There is no shared email-town-labeler client and no email-town-labeler server: the OAuth client lives in a
Google Cloud project you own, and the token it issues stays on your machine.

This is the one-time hurdle. Expect 10 minutes.

You need:

- the Google account whose mail you want labeled (personal `@gmail.com` or Google Workspace);
- a browser;
- optional: the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install) (`gcloud`) to
  automate steps 1-2.

Google has reorganized the console: the OAuth consent screen now lives under
**Google Auth platform** with the pages **Branding**, **Audience**, **Data Access** and
**Clients**. Older guides that say "APIs & Services > OAuth consent screen" describe the same
settings.

## Fast path: the script

```bash
gcloud auth login                       # once, as the account that will own the project
scripts/gcp_setup.sh                    # macOS / Linux
# or
powershell -ExecutionPolicy Bypass -File scripts\gcp_setup.ps1   # Windows
```

The script creates a project (default id `email-town-labeler-<8 hex chars>`, derived from your account
so re-runs find the same project; override with `--project ID` / `-Project ID`), enables the
Gmail API, and prints direct links for steps 3-6, which Google only offers in the web console.
It is idempotent: running it again changes nothing that is already in place. `--dry-run` /
`-DryRun` prints the commands without running them.

The scripts live in a clone of the repository; they are not installed with the package. Without
a clone, print the same commands and links and paste them yourself:

```bash
email-town-labeler gcp-setup --print --project my-email-town-labeler
```

Then continue at [step 3](#3-configure-the-consent-screen).

## Manual path

### 1. Create a project

Open <https://console.cloud.google.com/projectcreate>, name it `email-town-labeler`, click **Create**,
and make sure it is selected in the project picker at the top.

A billing account is not required for the Gmail API.

### 2. Enable the Gmail API

Open <https://console.cloud.google.com/apis/library/gmail.googleapis.com> and click **Enable**.

(`gcloud services enable gmail.googleapis.com --project YOUR_PROJECT` does the same.)

### 3. Configure the consent screen

Open **Google Auth platform > Branding**:
<https://console.cloud.google.com/auth/branding>. If it says *Google Auth platform not
configured yet*, click **Get started**:

1. **App information**: app name `email-town-labeler`; user support email: your address.
2. **Audience**: pick the user type.

   | Your account | Choose | Why |
   |---|---|---|
   | Google Workspace, and the project is in that Workspace organization | **Internal** | Only people in your organization can authorize it. No test-user list, no 7-day token expiry, no verification. Your Workspace admin may have to allow the app, because `gmail.modify` is a restricted scope. |
   | Personal `@gmail.com` (or a Workspace account using a project outside its org) | **External** | Internal is only offered for projects that belong to a Google Cloud organization. |

3. **Contact information**: your address.
4. **Finish**: accept the Google API Services User Data Policy, then **Create**.

### 4. Audience: testing vs. production (External only)

This step decides whether you have to log in again every week. Read it.

Open **Google Auth platform > Audience**: <https://console.cloud.google.com/auth/audience>.

A new External app starts in **Testing** status. Two facts from Google's documentation:

- In Testing, only accounts listed under **Test users** (up to 100) can authorize the app.
- **In Testing, authorizations expire seven days after consent, and so does the refresh
  token.** The only exception is apps that request nothing beyond name, email and profile;
  email-town-labeler needs Gmail access, so the exception does not apply.

So pick one:

**Option A: stay in Testing, re-login weekly.**
Under **Test users**, click **Add users**, add your own Gmail address, **Save**. Every 7 days
email-town-labeler's token stops working; `email-town-labeler status` and `email-town-labeler doctor` report it, and the
service logs an auth error. Run `email-town-labeler login` again (or
`docker compose run --rm email-town-labeler login --headless`).

**Option B (recommended for personal Gmail): publish to "In production", unverified.**
Click **Publish app** and confirm. Because email-town-labeler requests `gmail.modify`, a *restricted*
scope, and you will not submit the app for Google verification:

- Google shows a **"Google hasn't verified this app"** warning during login. Click
  **Advanced**, then **Go to email-town-labeler (unsafe)**. You are the developer of this app; the
  warning exists because Google has not reviewed it.
- The app is limited to **100 users in total** over the project's lifetime. Google explicitly
  allows unverified apps for personal use with fewer than 100 users.
- Refresh tokens **no longer expire after 7 days**. They still end if you revoke access,
  change your Google password (tokens with Gmail scopes are revoked on password change), or
  leave the token unused for six months.

Do not submit this project for verification. Verification of a restricted scope involves a
security assessment and is meant for apps distributed to the public; a personal tool does not
need it.

### 5. Add the Gmail scope

Open **Google Auth platform > Data Access**: <https://console.cloud.google.com/auth/scopes>.
Click **Add or remove scopes**, paste this into **Manually add scopes**, click **Add to
table**, tick it, **Update**, then **Save**:

```
https://www.googleapis.com/auth/gmail.modify
```

It appears under **Your restricted scopes**. That is expected. email-town-labeler requests exactly this
one scope and nothing else. (Listing it here is what makes the consent screen match what the
app asks for; a mismatch triggers extra warnings.)

Why `gmail.modify` and not something narrower: email-town-labeler has to read a message to classify it,
and add or remove labels on it. `gmail.labels` alone cannot read messages; `gmail.readonly`
cannot change labels. `gmail.modify` covers both and, unlike `https://mail.google.com/`, cannot
permanently delete mail. See [privacy.md](privacy.md#what-gmailmodify-allows-and-what-email-town-labeler-does)
for what the code actually does with it.

### 6. Create a Desktop OAuth client and download it

Open **Google Auth platform > Clients**: <https://console.cloud.google.com/auth/clients>.

1. **Create client**.
2. **Application type: Desktop app**. Not "Web application": email-town-labeler uses the installed-app
   loopback flow (`http://localhost` redirect with PKCE), which only Desktop clients support
   without configuring redirect URIs.
3. Name: `email-town-labeler desktop`. Leave *Use this client for an AI-powered agent* unticked.
4. **Create**, then **Download JSON** in the dialog (or later from the client's page).

The file is named like `client_secret_1234-abc.apps.googleusercontent.com.json`. Google
documents that a Desktop client "secret" is not actually confidential, but treat the file as
private anyway: anyone with it can show your consent screen under your app's name.

### 7. Hand it to email-town-labeler

```bash
email-town-labeler setup
```

The wizard asks for the path to the downloaded JSON, copies it to
`<config dir>/client_secret.json`, opens the browser for consent, and stores the token in
`<config dir>/token.json` (file mode 0600 on macOS/Linux). See
[configuration.md](configuration.md#files-and-directories) for where `<config dir>` is on your
OS.

On a machine without a browser (server, SSH, Docker), use `email-town-labeler login --headless`: it
prints a URL; open it on any device, approve, and paste back the full `http://localhost...`
URL your browser ends up on. That page will fail to load. That is expected; the code you need
is in its address.

## Quota

From Google's [Gmail API usage limits](https://developers.google.com/workspace/gmail/api/reference/quota)
(checked 2026-10-22):

| Limit | Value |
|---|---|
| Per user per project | 6,000 quota units per minute |
| Per project | 1,200,000 quota units per minute |
| Daily billing threshold, per project | 80,000,000 units per day |

Costs of the calls email-town-labeler makes:

| Call | Units | When |
|---|---|---|
| `history.list` | 2 | every poll (default every 60 s), per page |
| `messages.get` (`format=full`) | 60 | once per new message to classify |
| `messages.get` (`format=minimal`) | 20 | label checks (feedback, undo) |
| `messages.get` (`format=metadata`) | see Google's table | To/Cc/Bcc of sent mail for contacts: once at setup (at most `contacts_scan_limit`, default 5,000), then per message you send; paced at 240 a minute |
| `messages.list` | 5 | backfill and history-expired recovery, per page |
| `messages.modify` | 5 | once per labeled message |
| `labels.list` / `labels.create` | 1 / 5 | startup / first use of a label |
| `getProfile` | 1 | first run, `doctor` |

What that means:

- **Steady state is far below the limit.** An idle poll costs 2 units a minute. A message
  costs about 65 units (fetch + label). The per-user limit allows roughly 90 new messages a
  minute before email-town-labeler has to back off.
- **Backfill is quota-bound at roughly 90 messages a minute (about 5,000 an hour).**
  Batching requests saves HTTP round trips but not quota units. email-town-labeler retries
  `429` / `rateLimitExceeded` / `userRateLimitExceeded` with exponential backoff, so a large
  `backfill` slows down instead of failing.
- **The daily threshold is not a practical concern for one mailbox.** Running at the per-user
  maximum all day is 8.6 M units, about a tenth of the per-project threshold. Google states
  that use under the threshold incurs no charges, and that billing details for usage above
  it will be announced later in 2026 with at least 90 days' notice.

## Revoking access

Go to <https://myaccount.google.com/permissions>, select **email-town-labeler**, and remove access.
Then delete `token.json` (or the Docker volume). Deleting the Google Cloud project also
invalidates every token it issued.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Error 403: access_denied`, "has not completed the Google verification process" | External app in Testing and your account is not a test user | Add yourself under **Audience > Test users**, or publish the app (step 4). |
| Works for a week, then `invalid_grant` / "Token has been expired or revoked" | External app still in Testing (7-day expiry), or you changed your password | `email-town-labeler login`; publish the app to stop the weekly expiry. |
| `Error 400: redirect_uri_mismatch` | OAuth client is "Web application" | Create a **Desktop app** client (step 6) and run `setup` again. |
| `org_internal` error | Internal app, but the account is outside the project's organization | Use an account in that org, or switch **Audience** to External. |
| `admin_policy_enforced` | Workspace admin blocks restricted Gmail scopes for third-party or internal apps | Ask the admin to allow the app's client ID (Admin console > Security > API controls). |
| `accessNotConfigured` / "Gmail API has not been used in project" | Gmail API not enabled | Step 2. It can take a minute to propagate. |
| "Google hasn't verified this app" | Expected for an unverified app in production | **Advanced > Go to email-town-labeler (unsafe)**. |

More in [troubleshooting.md](troubleshooting.md).

## Sources

All checked on 2026-10-22:

- Refresh token expiration, including the 7-day rule for External + Testing:
  <https://developers.google.com/identity/protocols/oauth2#expiration>
- Publishing status, test users, 7-day authorization expiry, user cap:
  <https://support.google.com/cloud/answer/15549945>
- Unverified apps and the 100-user cap: <https://support.google.com/cloud/answer/7454865>
- Personal-use exemption from verification (fewer than 100 users):
  <https://support.google.com/cloud/answer/13464323>
- Gmail scope classification (`gmail.modify` is restricted):
  <https://developers.google.com/workspace/gmail/api/auth/scopes>
- Desktop app flow (loopback redirect, PKCE; manual copy/paste flow deprecated):
  <https://developers.google.com/identity/protocols/oauth2/native-app>
- Consent screen configuration (Branding, Audience, Data Access):
  <https://developers.google.com/workspace/guides/configure-oauth-consent>
- Creating a Desktop client: <https://developers.google.com/workspace/guides/create-credentials#desktop-app>
- Gmail API quotas: <https://developers.google.com/workspace/gmail/api/reference/quota>
