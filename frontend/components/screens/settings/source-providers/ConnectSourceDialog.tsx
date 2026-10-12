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
import { useTranslations } from "next-intl";
import type { ScmConnectionKind, ScmVisibilityScope } from "@/graphql/scm/scm.types";

import type { useConnectSource } from "./use-connect-source";

export type ConnectSourceDialogViewProps = ReturnType<typeof useConnectSource> & {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

// Labels and hints live under sourceProviders.connect.kinds.<value>.
const KINDS: {
  value: ScmConnectionKind;
  takesOauthApp: boolean;
}[] = [
  {
    value: "github_oauth_app",
    takesOauthApp: true,
  },
  {
    value: "github_app_install",
    takesOauthApp: false,
  },
  {
    value: "github_pat",
    takesOauthApp: false,
  },
  {
    value: "gitlab_oauth_app",
    takesOauthApp: true,
  },
  {
    value: "gitlab_pat",
    takesOauthApp: false,
  },
  {
    value: "bitbucket_oauth_app",
    takesOauthApp: true,
  },
  {
    value: "bitbucket_pat",
    takesOauthApp: false,
  },
  {
    value: "gitea_oauth_app",
    takesOauthApp: true,
  },
  {
    value: "gitea_pat",
    takesOauthApp: false,
  },
];

// Labels live under sourceProviders.connect.scopes.<value>.
const VISIBILITY_SCOPES: ScmVisibilityScope[] = [
  "private_org",
  "public_org",
  "user_repos",
  "public_non_org",
];

/** "Connect a source host": the generic any-kind connect sheet. */
export function ConnectSourceDialogView(props: ConnectSourceDialogViewProps) {
  return props.open ? <ConnectSourceDialogViewForm {...props} /> : null;
}

function ConnectSourceDialogViewForm({
  open,
  onOpenChange,
  loading,
  onConnect,
}: ConnectSourceDialogViewProps) {
  const t = useTranslations("sourceProviders.connect");
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
          <SheetTitle>{t("title")}</SheetTitle>
          <SheetDescription>
            {t.rich("description", {
              code: (chunks) => <code>{chunks}</code>,
              guide: (chunks) => (
                <Link href={DOC_LINKS.sourceProviders} className="underline">
                  {chunks}
                </Link>
              ),
            })}
          </SheetDescription>
        </SheetHeader>
        <form onSubmit={submit} className="flex flex-1 flex-col gap-4 overflow-y-auto px-4 pb-4">
          <div className="space-y-2">
            <Label htmlFor="kind">{t("kind")}</Label>
            <Select value={kind} onValueChange={(v) => setKind(v as ScmConnectionKind)}>
              <SelectTrigger id="kind">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {KINDS.map((k) => (
                  <SelectItem key={k.value} value={k.value}>
                    {t(`kinds.${k.value}.label`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {meta && (
              <p className="text-muted-foreground text-xs">{t(`kinds.${meta.value}.hint`)}</p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="display-name">{t("displayName")}</Label>
            <Input
              id="display-name"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder={t("displayNamePlaceholder")}
            />
          </div>

          {!isOauthApp && (
            <div className="space-y-2">
              <Label htmlFor="account">{t("account")}</Label>
              <Input
                id="account"
                value={accountLogin}
                onChange={(e) => setAccountLogin(e.target.value)}
                placeholder={t("accountPlaceholder")}
                className="font-mono text-xs"
              />
            </div>
          )}

          {kind === "github_app_install" && (
            <div className="space-y-2">
              <Label htmlFor="installation-id">{t("installationId")}</Label>
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
              <Label htmlFor="api-base-url">{t("apiBaseUrl")}</Label>
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
                  {isGithub ? t("githubAppId") : t("oauthClientId")}
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
                  <p className="text-muted-foreground text-xs">{t("githubAppIdHelp")}</p>
                )}
              </div>
              <div className="space-y-2">
                <Label htmlFor="oauth-redirect">{t("redirectUri")}</Label>
                <Input
                  id="oauth-redirect"
                  value={oauthRedirectUri}
                  onChange={(e) => setOauthRedirectUri(e.target.value)}
                  placeholder={`https://your-astrolift.example${redirectUriHostPath}`}
                  type="url"
                  className="font-mono text-xs"
                />
                <p className="text-muted-foreground text-xs">
                  {t("redirectUriHelp", { host: isGitlab ? "GitLab" : "GitHub" })}
                </p>
              </div>
            </>
          )}

          {needsAppClientId && (
            <div className="space-y-2">
              <Label htmlFor="app-client-id">{t("appClientId")}</Label>
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
                {t.rich("appClientIdHelp", {
                  code: (chunks) => <code>{chunks}</code>,
                  link: (chunks) => (
                    <a
                      href="https://github.com/settings/apps"
                      target="_blank"
                      rel="noreferrer noopener"
                      className="underline"
                    >
                      {chunks}
                    </a>
                  ),
                })}
              </p>
              {!appClientIdOk && appClientId !== "" && (
                <p className="text-destructive text-xs">
                  {t.rich("appClientIdInvalid", { code: (chunks) => <code>{chunks}</code> })}
                </p>
              )}
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="secret">
              {isOauthApp
                ? t("secretOauth")
                : kind === "github_app_install"
                  ? t("secretPem")
                  : t("secretToken")}
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
            <p className="text-muted-foreground text-xs">{t("secretHelp")}</p>
          </div>

          {(isGithub || isGitlab) && (
            <div className="space-y-2">
              <Label>{t("visibility")}</Label>
              <p className="text-muted-foreground text-xs">
                {t("visibilityHelp", { host: isGitlab ? "GitLab" : "GitHub" })}
              </p>
              <div className="grid grid-cols-2 gap-2">
                {VISIBILITY_SCOPES.map((s) => (
                  <label key={s} className="flex items-center gap-2 text-xs">
                    <input
                      type="checkbox"
                      checked={scopes.includes(s)}
                      onChange={() => toggleScope(s)}
                    />
                    {t(`scopes.${s}`)}
                  </label>
                ))}
              </div>
            </div>
          )}

          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {t("cancel")}
            </Button>
            <Button
              type="submit"
              disabled={
                loading || !secret || (needsAppClientId && (!appClientId.trim() || !appClientIdOk))
              }
            >
              {loading ? t("connecting") : t("connect")}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
