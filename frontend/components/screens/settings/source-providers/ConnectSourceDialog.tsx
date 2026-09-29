"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { DOC_LINKS } from "@/lib/docs/urls";
import Link from "next/link";
import type { ScmConnectionKind, ScmVisibilityScope } from "@/graphql/scm/scm.types";

import type { useConnectSource } from "./use-connect-source";

export type ConnectSourceDialogViewProps = ReturnType<typeof useConnectSource> & {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

const KINDS: {
  value: ScmConnectionKind;
  label: string;
  hint: string;
  takesOauthApp: boolean;
}[] = [
  {
    value: "github_oauth_app",
    label: "GitHub OAuth App config",
    hint: "Operator-registered OAuth app. Holds client_id + client_secret. Per-user tokens get stored as separate connections after the OAuth dance (phase 2).",
    takesOauthApp: true,
  },
  {
    value: "github_app_install",
    label: "GitHub App installation",
    hint: "Per-org App installation. Secret holds the App's private-key PEM; we mint per-installation tokens at deploy time.",
    takesOauthApp: false,
  },
  {
    value: "github_pat",
    label: "GitHub PAT",
    hint: "Paste a Personal Access Token (fine-grained or classic). Simplest path; tied to the user that minted it.",
    takesOauthApp: false,
  },
  {
    value: "gitlab_oauth_app",
    label: "GitLab OAuth App config",
    hint: "GitLab OAuth Application. SaaS gitlab.com or self-hosted (set api_base_url).",
    takesOauthApp: true,
  },
  {
    value: "gitlab_pat",
    label: "GitLab PAT",
    hint: "GitLab personal or project access token. Self-hosted: set api_base_url.",
    takesOauthApp: false,
  },
  {
    value: "bitbucket_oauth_app",
    label: "Bitbucket OAuth Consumer",
    hint: "Bitbucket OAuth 2.0 Consumer.",
    takesOauthApp: true,
  },
  {
    value: "bitbucket_pat",
    label: "Bitbucket app password",
    hint: "Bitbucket app password (workspace-scoped).",
    takesOauthApp: false,
  },
  {
    value: "gitea_oauth_app",
    label: "Gitea OAuth App",
    hint: "Self-hosted Gitea instance.",
    takesOauthApp: true,
  },
  {
    value: "gitea_pat",
    label: "Gitea token",
    hint: "Self-hosted Gitea API token.",
    takesOauthApp: false,
  },
];

const VISIBILITY_SCOPES: { value: ScmVisibilityScope; label: string }[] = [
  { value: "private_org", label: "private repos in connected org" },
  { value: "public_org", label: "public repos in connected org" },
  { value: "user_repos", label: "user repos" },
  { value: "public_non_org", label: "public repos outside the org" },
];

/** "Connect a source host": the generic any-kind connect sheet. */
export function ConnectSourceDialogView({
  open,
  onOpenChange,
  loading,
  onConnect,
}: ConnectSourceDialogViewProps) {
  const [kind, setKind] = React.useState<ScmConnectionKind>("github_pat");
  const [displayName, setDisplayName] = React.useState("");
  const [accountLogin, setAccountLogin] = React.useState("");
  const [installationId, setInstallationId] = React.useState("");
  const [apiBaseUrl, setApiBaseUrl] = React.useState("");
  const [secret, setSecret] = React.useState("");
  const [oauthClientId, setOauthClientId] = React.useState("");
  // GitHub App OAuth Client ID — distinct from the numeric App ID for
  // github_app_install rows. Required for the user-to-server OAuth
  // dance + JWT iss claim. See #525.
  const [appClientId, setAppClientId] = React.useState("");
  const [oauthRedirectUri, setOauthRedirectUri] = React.useState("");
  const [scopes, setScopes] = React.useState<ScmVisibilityScope[]>([]);

  React.useEffect(() => {
    if (!open) {
      setKind("github_pat");
      setDisplayName("");
      setAccountLogin("");
      setInstallationId("");
      setApiBaseUrl("");
      setSecret("");
      setOauthClientId("");
      setAppClientId("");
      setOauthRedirectUri("");
      setScopes([]);
    }
  }, [open]);

  const meta = KINDS.find((k) => k.value === kind);
  const isOauthApp = meta?.takesOauthApp ?? false;
  const isGithub = kind.startsWith("github_");
  const isGitlab = kind.startsWith("gitlab_");
  // Both github_oauth_app + github_app_install need the user-to-server
  // OAuth Client ID for the "Connect my GitHub" dance to work.
  const needsAppClientId = kind === "github_oauth_app" || kind === "github_app_install";
  // Client ID format check (UX hint only — backend re-validates).
  // ``Iv…`` for new GitHub Apps; 20-char lowercase hex for legacy
  // OAuth Apps. Empty string is allowed here (the submit button
  // disables itself when needsAppClientId + empty).
  const appClientIdOk =
    appClientId === "" ||
    /^Iv\d+[A-Za-z0-9]+$/.test(appClientId.trim()) ||
    /^[a-f0-9]{20}$/.test(appClientId.trim());

  // OAuth-app config rows need a redirect URI that matches the host
  // — the placeholder hints the operator at the correct backend path
  // for the kind they just selected so they're less likely to paste
  // a stale GitHub URL into a GitLab Application.
  const redirectUriHostPath = isGitlab
    ? "/app/auth1/scm/gitlab/callback"
    : "/app/auth1/scm/github/callback";

  function toggleScope(s: ScmVisibilityScope) {
    setScopes((prev) => (prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]));
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!secret) return;
    if (needsAppClientId && (!appClientId.trim() || !appClientIdOk)) return;
    const ok = await onConnect({
      kind,
      displayName: displayName || null,
      accountLogin: accountLogin || null,
      installationId: installationId || null,
      apiBaseUrl: apiBaseUrl || null,
      secretPlaintext: secret,
      oauthClientId: oauthClientId || null,
      appClientId: appClientId.trim() || null,
      oauthRedirectUri: oauthRedirectUri || null,
      repoVisibilityScopes: scopes,
    });
    if (ok) onOpenChange(false);
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle>Connect a source host</SheetTitle>
          <SheetDescription>
            Tokens and OAuth client secrets are encrypted at rest via the platform secrets backend
            (default: local Fernet derived from
            <code> SECRET_KEY</code>; switch to AWS Secrets Manager / GCP Secret Manager / Azure Key
            Vault per install).{" "}
            <Link href={DOC_LINKS.sourceProviders} className="underline">
              Source-provider setup guide
            </Link>
            .
          </SheetDescription>
        </SheetHeader>
        <form onSubmit={submit} className="flex flex-1 flex-col gap-4 overflow-y-auto px-4 pb-4">
          <div className="space-y-2">
            <Label htmlFor="kind">Kind</Label>
            <Select value={kind} onValueChange={(v) => setKind(v as ScmConnectionKind)}>
              <SelectTrigger id="kind">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {KINDS.map((k) => (
                  <SelectItem key={k.value} value={k.value}>
                    {k.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {meta && <p className="text-muted-foreground text-xs">{meta.hint}</p>}
          </div>

          <div className="space-y-2">
            <Label htmlFor="display-name">Display name (optional)</Label>
            <Input
              id="display-name"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="e.g. Production GitHub, Acme corp GitLab"
            />
          </div>

          {!isOauthApp && (
            <div className="space-y-2">
              <Label htmlFor="account">Account / org login</Label>
              <Input
                id="account"
                value={accountLogin}
                onChange={(e) => setAccountLogin(e.target.value)}
                placeholder="acme-org or alice"
                className="font-mono text-xs"
              />
            </div>
          )}

          {kind === "github_app_install" && (
            <div className="space-y-2">
              <Label htmlFor="installation-id">Installation ID</Label>
              <Input
                id="installation-id"
                value={installationId}
                onChange={(e) => setInstallationId(e.target.value)}
                placeholder="123456789"
                className="font-mono text-xs"
              />
            </div>
          )}

          {!kind.startsWith("github_") && (
            <div className="space-y-2">
              <Label htmlFor="api-base-url">API base URL (self-hosted)</Label>
              <Input
                id="api-base-url"
                value={apiBaseUrl}
                onChange={(e) => setApiBaseUrl(e.target.value)}
                placeholder="https://gitlab.acme.example"
                type="url"
                className="font-mono text-xs"
              />
            </div>
          )}

          {isOauthApp && (
            <>
              <div className="space-y-2">
                <Label htmlFor="oauth-client-id">
                  {isGithub
                    ? "GitHub App ID (numeric — webhook payload lookups)"
                    : "OAuth Client ID"}
                </Label>
                <Input
                  id="oauth-client-id"
                  value={oauthClientId}
                  onChange={(e) => setOauthClientId(e.target.value)}
                  placeholder={isGithub ? "3705068" : ""}
                  className="font-mono text-xs"
                  required
                />
                {isGithub && (
                  <p className="text-muted-foreground text-xs">
                    The numeric App ID GitHub shows on your App settings page. Used for
                    webhook-payload App lookups; the user-to-server OAuth Client ID is separate
                    (below).
                  </p>
                )}
              </div>
              <div className="space-y-2">
                <Label htmlFor="oauth-redirect">OAuth redirect URI</Label>
                <Input
                  id="oauth-redirect"
                  value={oauthRedirectUri}
                  onChange={(e) => setOauthRedirectUri(e.target.value)}
                  placeholder={`https://your-astrolift.example${redirectUriHostPath}`}
                  type="url"
                  className="font-mono text-xs"
                />
                <p className="text-muted-foreground text-xs">
                  Must match the redirect URI registered on the OAuth application at the host side (
                  {isGitlab ? "GitLab" : "GitHub"}).
                </p>
              </div>
            </>
          )}

          {needsAppClientId && (
            <div className="space-y-2">
              <Label htmlFor="app-client-id">GitHub App Client ID</Label>
              <Input
                id="app-client-id"
                value={appClientId}
                onChange={(e) => setAppClientId(e.target.value)}
                placeholder="Iv23lic8662KXwe4XKEI"
                className="font-mono text-xs"
                autoComplete="off"
                required
              />
              <p className="text-muted-foreground text-xs">
                Find this on your GitHub App settings page — looks like <code>Iv23l...</code> for
                new GitHub Apps or a 20-char hex string for legacy OAuth Apps.{" "}
                <a
                  href="https://github.com/settings/apps"
                  target="_blank"
                  rel="noreferrer noopener"
                  className="underline"
                >
                  Open GitHub App settings →
                </a>
              </p>
              {!appClientIdOk && appClientId !== "" && (
                <p className="text-destructive text-xs">
                  Doesn&apos;t look like a GitHub App Client ID — expected <code>Iv…</code> or a
                  20-char hex string.
                </p>
              )}
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="secret">
              {isOauthApp
                ? "OAuth Client Secret"
                : kind === "github_app_install"
                  ? "GitHub App private-key PEM"
                  : "Personal Access Token / app password"}
            </Label>
            <Input
              id="secret"
              type="password"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              className="font-mono text-xs"
              required
              autoComplete="off"
            />
            <p className="text-muted-foreground text-xs">
              Stored encrypted at rest. Never logged. Rotate by editing this connection later.
            </p>
          </div>

          {(isGithub || isGitlab) && (
            <div className="space-y-2">
              <Label>Repo visibility scopes</Label>
              <p className="text-muted-foreground text-xs">
                Constrain what repos this connection is allowed to surface when listing queries call
                the {isGitlab ? "GitLab" : "GitHub"} API. No selections = no scope restriction.
              </p>
              <div className="grid grid-cols-2 gap-2">
                {VISIBILITY_SCOPES.map((s) => (
                  <label key={s.value} className="flex items-center gap-2 text-xs">
                    <input
                      type="checkbox"
                      checked={scopes.includes(s.value)}
                      onChange={() => toggleScope(s.value)}
                    />
                    {s.label}
                  </label>
                ))}
              </div>
            </div>
          )}

          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={
                loading || !secret || (needsAppClientId && (!appClientId.trim() || !appClientIdOk))
              }
            >
              {loading ? "Connecting…" : "Connect"}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
