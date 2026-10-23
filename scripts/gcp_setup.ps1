<#
.SYNOPSIS
  Prepare a Google Cloud project for email-town-labeler (Windows / PowerShell 5.1+ or 7+).

.DESCRIPTION
  Automates what gcloud can do (create project, enable the Gmail API) and prints the exact
  console URLs for the steps Google only offers in the web console (OAuth consent screen,
  Desktop OAuth client). Safe to re-run: every step checks before it changes anything.

  Requires the Google Cloud CLI (https://cloud.google.com/sdk/docs/install) and
  `gcloud auth login` with the Google account that should own the project.
  Full guide: docs/google-cloud-setup.md

.EXAMPLE
  .\scripts\gcp_setup.ps1
  .\scripts\gcp_setup.ps1 -Project my-email-town-labeler -DryRun
#>
[CmdletBinding()]
param(
  [string]$Project = $env:EMAIL_TOWN_LABELER_GCP_PROJECT,
  [switch]$DryRun
)

# Equivalent of `set -euo pipefail`.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -ge 7 -and (Get-Variable PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue)) {
  $PSNativeCommandUseErrorActionPreference = $false   # we check $LASTEXITCODE explicitly
}

function Invoke-Gcloud {
  # Print, then run unless -DryRun. Throws on non-zero exit.
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GcloudArgs)
  Write-Host ("+ gcloud " + ($GcloudArgs -join ' '))
  if ($DryRun) { return }
  & gcloud @GcloudArgs
  if ($LASTEXITCODE -ne 0) { throw "gcloud $($GcloudArgs -join ' ') failed with exit code $LASTEXITCODE" }
}

function Test-Gcloud {
  # Run quietly; return $true on exit code 0.
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GcloudArgs)
  $out = & gcloud @GcloudArgs 2>$null
  return @{ Ok = ($LASTEXITCODE -eq 0); Out = ($out -join "`n") }
}

if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
  Write-Error "gcloud not found. Install it from https://cloud.google.com/sdk/docs/install or follow docs/google-cloud-setup.md."
}

$account = (Test-Gcloud auth list --filter=status:ACTIVE '--format=value(account)').Out
$account = if ($account) { ($account -split "`n")[0].Trim() } else { '' }
if (-not $account) {
  if ($DryRun) { $account = 'you@example.com'; Write-Host "(dry run) no active gcloud account; pretending to be $account" }
  else { Write-Error 'No active gcloud account. Run: gcloud auth login' }
}

if (-not $Project) {
  # Deterministic per account so re-running finds the same project instead of making a new one.
  $sha = [System.Security.Cryptography.SHA256]::Create()
  $hash = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($account))
  $suffix = (-join ($hash | ForEach-Object { $_.ToString('x2') })).Substring(0, 8)
  $Project = "email-town-labeler-$suffix"
}

if ($Project -cnotmatch '^[a-z][a-z0-9-]{4,28}[a-z0-9]$') {
  Write-Host "error: '$Project' is not a valid project id (6-30 chars: a-z, 0-9, '-', start with a letter)"
  exit 2
}

Write-Host "account: $account"
Write-Host "project: $Project"
Write-Host ''

# 1. Project
if ((Test-Gcloud projects describe $Project '--format=value(projectId)').Ok) {
  Write-Host "[ok] project $Project exists"
} else {
  Write-Host "[..] creating project $Project"
  Invoke-Gcloud projects create $Project --name=email-town-labeler
}

# 2. Gmail API
$enabled = $false
if (-not $DryRun) {
  $r = Test-Gcloud services list --enabled --project $Project '--filter=config.name=gmail.googleapis.com' '--format=value(config.name)'
  $enabled = $r.Ok -and ($r.Out -match 'gmail\.googleapis\.com')
}
if ($enabled) {
  Write-Host '[ok] gmail.googleapis.com enabled'
} else {
  Write-Host '[..] enabling gmail.googleapis.com'
  Invoke-Gcloud services enable gmail.googleapis.com --project $Project
}

$c = 'https://console.cloud.google.com'
$q = "?project=$Project"
Write-Host @"

Done with the parts gcloud can automate. Finish these in the browser
(walkthrough: docs/google-cloud-setup.md):

  3. Configure the consent screen (Google Auth platform -> Get started / Branding):
       $c/auth/overview$q
     App name: email-town-labeler. Support + contact email: your address.
     Audience: Internal  if this is a Google Workspace account in your own organization;
               External  for personal @gmail.com accounts.

  4. Audience (External only): add yourself as a test user, or click "Publish app".
       $c/auth/audience$q
     Testing status => refresh tokens expire after 7 days (weekly re-login).
     In production (unverified, personal use) => no 7-day expiry; Google shows an
     "unverified app" warning you click through once.

  5. Data Access -> Add or remove scopes -> add:
       https://www.googleapis.com/auth/gmail.modify
       $c/auth/scopes$q

  6. Clients -> Create client -> Application type "Desktop app" -> Create -> Download JSON:
       $c/auth/clients/create$q

  7. Then:
       email-town-labeler setup          # it asks for the downloaded JSON file

"@
