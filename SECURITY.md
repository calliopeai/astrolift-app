# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Astrolift, please report it responsibly.

**Do not open a public issue.**

Instead, email **security@astrolift.app** with:

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

We will acknowledge your report within 48 hours and aim to release a fix within 7 days for critical issues.

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
