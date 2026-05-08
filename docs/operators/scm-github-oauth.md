# Connecting Astrolift to GitHub via OAuth

Operator setup. Run through this once after deploying Astrolift; users
can then connect their personal GitHub accounts from the UI without
operator involvement.

## What this gives you

After this is wired up:

- Each user clicks **Connect my GitHub** on `/settings/source-providers`
  and authorizes Astrolift through GitHub's OAuth flow.
- Astrolift stores a per-user access token (encrypted at rest) tagged
  back to your registered OAuth App.
- `/apps/new` lists each user's repos through their own token —
  private repos the user can see, filtered by the visibility scopes
  the operator chose.
- Audit rows attribute every clone/push to the user that authorized
  it, not a shared bot account.

There are three other ways to authenticate to GitHub (PAT, GitHub App
installation, SSH deploy keys) that don't need this OAuth dance. This
guide is specifically for the per-user "Connect my GitHub" experience.

## Prerequisites

- An Astrolift deployment reachable on a public URL (or `http://localhost:3000`
  for local dev). GitHub's OAuth callback has to be able to reach
  Astrolift, so a tunnel like `cloudflared` or `ngrok` is a fine
  stand-in for personal demo work.
- An admin (`scm.connect` permission) on the Astrolift org. The
  default `org_owner` and `org_admin` system roles include this.
- Access to a GitHub user or org account that can register OAuth Apps.

## Step 1 — Register the OAuth App on GitHub

1. Go to <https://github.com/settings/developers> (or your org's
   `Settings → Developer settings`).
2. Click **OAuth Apps → New OAuth App**.
3. Fill in:
   - **Application name**: anything you'll recognize (e.g.
     "Acme Astrolift").
   - **Homepage URL**: `https://<your-astrolift-host>` (or
     `http://localhost:3000` for local dev).
   - **Authorization callback URL**: this is non-negotiable —
     ```
     https://<your-astrolift-host>/app/auth1/scm/github/callback
     ```
     For local dev:
     ```
     http://localhost:3000/app/auth1/scm/github/callback
     ```
   - **Enable Device Flow**: leave off (Astrolift uses the standard
     web flow).
4. Click **Register application**.
5. On the resulting page:
   - Copy the **Client ID** (visible).
   - Click **Generate a new client secret** and copy the secret
     immediately — GitHub only shows it once.

Keep both tabs open; you'll paste them into Astrolift in the next step.

## Step 2 — Configure the OAuth App in Astrolift

1. Sign in to Astrolift as an admin.
2. Navigate to **Settings → Source providers**
   (`/settings/source-providers`).
3. Click **Connect host**.
4. In the sheet:
   - **Kind**: `GitHub OAuth App config`.
   - **Display name**: optional (e.g. "Acme GitHub OAuth App").
   - **OAuth Client ID**: paste the Client ID from Step 1.
   - **OAuth redirect URI**: paste the same callback URL you used
     when registering the app on GitHub:
     ```
     https://<your-astrolift-host>/app/auth1/scm/github/callback
     ```
   - **OAuth Client Secret**: paste the secret you copied from
     GitHub. Astrolift encrypts it at rest via the platform secrets
     backend (see `core.secrets` — local Fernet by default; swap to
     a cloud KMS via `ASTROLIFT_SECRETS_BACKEND`).
   - **Repo visibility scopes**: pick the constraints you want every
     user-token derived from this app to honor. Combinations are OR'd:
     - `private repos in connected org` — match the user-token owner
       to your GitHub org and only surface private repos there.
     - `public repos in connected org` — same match, public repos.
     - `user repos` — repos the user owns personally.
     - `public repos outside the org` — anything the user can see
       that isn't yours.
     Leave empty for no restriction.
5. Click **Connect**.

You should now see a row in the Hosts table tagged `OAuth-app config`
with a **Connect my GitHub** action.

## Step 3 — Test the user dance

1. Still as an admin (or any signed-in user with `scm.connect`),
   click **Connect my GitHub** on the row from Step 2.
2. The browser is redirected to `github.com/login/oauth/authorize`,
   which prompts you to authorize the OAuth App. Click **Authorize
   Astrolift** (or whatever name you chose).
3. GitHub redirects back to Astrolift's callback. Astrolift exchanges
   the code for an access token, stores it encrypted, and redirects
   you back to `/settings/source-providers` with a `?scm_connected=<your-login>`
   toast.
4. The Hosts table now has a second row tagged
   `personal · <your-login>` with kind `GitHub (OAuth user token)`.
5. Navigate to `/apps/new`. Pick the new connection from the
   "Pick from a connected host" panel — your repos should populate
   the dropdown.

Repeat Step 3 for each user; each click of **Connect my GitHub**
creates that user's own personal connection row.

## Troubleshooting

If the dance fails the URL comes back with `?scm_error=<code>`:

| Code                | What it means | Fix |
|---------------------|---------------|-----|
| `missing_config_id` | `Connect my GitHub` was clicked without a config row reference. | Don't hand-craft the URL; click the button on the OAuth-app row. |
| `config_not_found`  | The OAuth-app row was deleted or belongs to a different org. | Reconfigure Step 2 in the org you're signed into. |
| `config_incomplete` | The row's `oauth_client_id` is blank. | Edit the row, paste the Client ID from GitHub. |
| `state_mismatch`    | CSRF check failed — the session lost the state token (e.g. the user spent >2h on GitHub's authorize page, or session was rotated). | Click the button again. |
| `no_code`           | GitHub didn't return a `code` (user denied authorization). | Click again, accept on GitHub. |
| `exchange_failed`   | Astrolift couldn't reach `github.com/login/oauth/access_token` or the request was rejected. | Check that the **callback URL** registered on GitHub *exactly* matches the **OAuth redirect URI** in Astrolift; both must include the protocol and the trailing path. |
| `exchange_no_token` | GitHub returned 200 but no `access_token`. Usually means the client secret is wrong (typo on paste). | Edit the row, rotate the secret on GitHub, re-paste. |
| `org_mismatch`      | The user's active org changed between the start and callback. | Sign out, sign back in, retry. |

## Rotating the client secret

1. In GitHub: `Settings → Developer settings → OAuth Apps → <your app>`,
   click **Generate a new client secret**, copy it, **revoke the old
   one**.
2. In Astrolift: edit the OAuth-app config row on
   `/settings/source-providers` and paste the new secret in
   **Rotate secret**. Existing user tokens keep working — they're
   tied to the access token, not the client secret. New OAuth dances
   start using the new secret immediately.

## Notes for production

- **HTTPS is mandatory** in production. GitHub will accept `http://localhost`
  callbacks for development but rejects non-localhost HTTP.
- **Set the `oauth_redirect_uri`** explicitly on the OAuth-app row.
  If you leave it blank Astrolift falls back to
  `request.build_absolute_uri(...)` which can pick up the wrong host
  behind a reverse proxy.
- **Public client secrets are a security incident**. Astrolift
  encrypts at rest and never logs the value. If your secrets backend
  is `local_fernet` (default), the encryption key is derived from
  `SECRET_KEY` via HKDF. Migrate to AWS Secrets Manager / GCP Secret
  Manager / Azure Key Vault by setting `ASTROLIFT_SECRETS_BACKEND`
  and running the (forthcoming) `migrate_secrets` command — see
  `core.secrets`.
- **Scopes**: Astrolift requests `read:user repo` from GitHub. If you
  don't want OAuth users to grant write access, register a separate
  OAuth App for read-only consumers and configure both — the
  visibility-scope policy on each app row constrains what gets
  surfaced regardless.
