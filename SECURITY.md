# Security Policy

## Reporting a Vulnerability

**This repository is private.** Everyone who can read this file already has
repository access, so the reliable channel today is a new issue in this
repository, titled with a `[security]` prefix. While the repository is
private that issue is not publicly visible.

Include:

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

We aim to acknowledge a report within 48 hours, and to release a fix within
7 days for critical issues.

### Before this repository becomes public

A repository issue stops being confidential the moment the repository is
public. Ahead of that, this policy has to move to GitHub private
vulnerability reporting, which is only available on public repositories.
Tracked in calliopeai/astrolift-app#1404.

Earlier revisions of this policy directed reports to an address on a domain
with no nameserver delegation and no MX record, so mail to it could never be
delivered and reports were lost silently. Do not reintroduce an email contact
here without first confirming the domain resolves and accepts mail.

## Supported Versions

| Version | Supported |
| ------- | --------- |
| latest  | Yes       |

## Security Best Practices

When operating an Astrolift control-plane install:

- Never commit secrets, access keys, or service-account credentials to the repository
- Treat `backend/config/local.env` as secret — it is gitignored; never copy real credentials into the tracked `example.env` or `ci-local.env`
- Use a managed secrets store (AWS Secrets Manager / GCP Secret Manager / Azure Key Vault / Kubernetes Secrets) for application secrets in any non-local environment
- Run the control plane with TLS terminated at the ingress (cert-manager + Let's Encrypt or your CA of choice)
- Auth0 (or your IdP) handles human auth; never disable the auth middleware in a non-local build
- The dev-login bypass (`NEXT_PUBLIC_DEV_LOGIN=1` + `DEBUG=True`) only works when Django `DEBUG=True`; production builds reject it with a 404
- Permission checks live at the resolver entry point (deny-by-default) — do not weaken or bypass them
- Use the `MutationResult { ok, errors, data? }` envelope on every mutation; never raise from a resolver or mutation
- Soft delete only on business models (`deleted_at`/`deleted_by`); never call `.delete()` on a model that inherits `Tracking`
- Rotate Auth0 client secrets, AWS / GCP / Azure access keys, and database credentials regularly
- Review `bootstrap.md` for the full data + auth + permissions model
