"use client";

import { CheckIcon, CopyIcon, ExternalLinkIcon, GitlabIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

import type { useConnectGitLab } from "./use-connect-gitlab";

export type ConnectGitLabDialogViewProps = ReturnType<typeof useConnectGitLab> & {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

/**
 * "Connect to GitLab" — guided Group OAuth Application creation.
 *
 * GitLab has no manifest-flow equivalent of GitHub's one-click App
 * creation, so we minimize friction with deep links + copy buttons:
 *
 *   1. Operator names their GitLab group (or instance URL for
 *      self-hosted). We surface a clickable link to the right "Add new
 *      application" page on GitLab and a copy button for the callback
 *      URL Astrolift expects (so they don't have to retype it).
 *   2. Operator creates the OAuth Application on GitLab, gets back an
 *      Application ID + Secret.
 *   3. Operator pastes those back into this form. We persist them as a
 *      gitlab_oauth_app SourceConnection row via the standard mutation.
 *
 * After persistence, the existing per-user "Connect my GitLab" button
 * on the connections table runs the user-side OAuth dance against the
 * row, same as today.
 */
export function ConnectGitLabDialogView(props: ConnectGitLabDialogViewProps) {
  return props.open ? <ConnectGitLabDialogViewForm {...props} /> : null;
}

function ConnectGitLabDialogViewForm({
  open,
  onOpenChange,
  loading,
  callbackUrl,
  copyCallback,
  onConnect,
}: ConnectGitLabDialogViewProps) {
  const t = useTranslations("sourceProviders.gitlab");
  const [groupOrInstance, setGroupOrInstance] = React.useState("");
  const [instanceUrl, setInstanceUrl] = React.useState("");
  const [clientId, setClientId] = React.useState("");
  const [clientSecret, setClientSecret] = React.useState("");
  const [copied, setCopied] = React.useState(false);

  const trimmedInstance = instanceUrl.trim().replace(/\/+$/, "");
  const instanceBase = trimmedInstance || "https://gitlab.com";
  const isSelfHosted = !!trimmedInstance && trimmedInstance !== "https://gitlab.com";
  const groupSlug = groupOrInstance.trim();

  const gitlabAppsUrl = groupSlug
    ? `${instanceBase}/groups/${encodeURIComponent(groupSlug)}/-/settings/applications`
    : `${instanceBase}/-/profile/applications`;

  async function onCopy() {
    if (await copyCallback()) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!clientId || !clientSecret) return;
    const displayName = groupSlug
      ? `GitLab ${isSelfHosted ? "(self-hosted)" : "SaaS"}: ${groupSlug}`
      : `GitLab ${isSelfHosted ? "(self-hosted)" : "SaaS"}`;
    const ok = await onConnect({
      kind: "gitlab_oauth_app",
      displayName,
      accountLogin: null,
      installationId: null,
      apiBaseUrl: isSelfHosted ? trimmedInstance : null,
      secretPlaintext: clientSecret,
      oauthClientId: clientId,
      oauthRedirectUri: callbackUrl,
      repoVisibilityScopes: [],
    });
    if (ok) onOpenChange(false);
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <GitlabIcon className="size-5" />
            {t("title")}
          </SheetTitle>
          <SheetDescription>{t("description")}</SheetDescription>
        </SheetHeader>

        <form onSubmit={submit} className="flex flex-1 flex-col gap-5 overflow-y-auto px-4 pb-4">
          {/* Step 1: group + instance */}
          <section className="space-y-3">
            <h3 className="text-sm font-semibold">
              <span className="text-muted-foreground mr-1.5">1.</span>
              {t("step1")}
            </h3>
            <div className="space-y-2">
              <Label htmlFor="gl-group">{t("group")}</Label>
              <Input
                id="gl-group"
                value={groupOrInstance}
                onChange={(e) => setGroupOrInstance(e.target.value)}
                placeholder={t("groupPlaceholder")}
                className="font-mono text-xs"
                autoComplete="off"
              />
              <p className="text-muted-foreground text-xs">{t("groupHelp")}</p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="gl-instance">{t("instance")}</Label>
              <Input
                id="gl-instance"
                value={instanceUrl}
                onChange={(e) => setInstanceUrl(e.target.value)}
                placeholder={t("instancePlaceholder")}
                type="url"
                className="font-mono text-xs"
                autoComplete="off"
              />
            </div>
          </section>

          {/* Step 2: deep link + callback URL */}
          <section className="space-y-3">
            <h3 className="text-sm font-semibold">
              <span className="text-muted-foreground mr-1.5">2.</span>
              {t("step2")}
            </h3>
            <Button asChild variant="outline" className="w-full justify-start">
              <a href={gitlabAppsUrl} target="_blank" rel="noreferrer">
                <ExternalLinkIcon className="size-4" />
                {t("openApps")}
                <span className="text-muted-foreground text-2xs ml-2 truncate font-mono">
                  {gitlabAppsUrl}
                </span>
              </a>
            </Button>
            <div className="space-y-1.5">
              <Label className="text-xs">{t("callbackLabel")}</Label>
              <div className="flex items-center gap-2">
                <code className="bg-muted text-2xs flex-1 rounded px-2 py-1 font-mono break-all">
                  {callbackUrl}
                </code>
                <Button type="button" size="sm" variant="outline" onClick={onCopy}>
                  {copied ? <CheckIcon className="size-4" /> : <CopyIcon className="size-4" />}
                  {copied ? t("copied") : t("copy")}
                </Button>
              </div>
              <p className="text-muted-foreground text-xs">
                {t.rich("scopes", {
                  code: (chunks) => <code className="font-mono">{chunks}</code>,
                  em: (chunks) => <em>{chunks}</em>,
                })}
              </p>
            </div>
          </section>

          {/* Step 3: paste back */}
          <section className="space-y-3">
            <h3 className="text-sm font-semibold">
              <span className="text-muted-foreground mr-1.5">3.</span>
              {t("step3")}
            </h3>
            <div className="space-y-2">
              <Label htmlFor="gl-client-id">{t("applicationId")}</Label>
              <Input
                id="gl-client-id"
                value={clientId}
                onChange={(e) => setClientId(e.target.value)}
                className="font-mono text-xs"
                autoComplete="off"
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="gl-client-secret">{t("secret")}</Label>
              <Input
                id="gl-client-secret"
                type="password"
                value={clientSecret}
                onChange={(e) => setClientSecret(e.target.value)}
                className="font-mono text-xs"
                autoComplete="off"
                required
              />
              <p className="text-muted-foreground text-xs">{t("secretHelp")}</p>
            </div>
          </section>

          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {t("cancel")}
            </Button>
            <Button type="submit" disabled={loading || !clientId || !clientSecret}>
              {loading ? t("connecting") : t("save")}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
