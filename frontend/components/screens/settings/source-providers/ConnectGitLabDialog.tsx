"use client";

import { CheckIcon, CopyIcon, ExternalLinkIcon, GitlabIcon } from "lucide-react";
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
export function ConnectGitLabDialogView({
  open,
  onOpenChange,
  loading,
  callbackUrl,
  copyCallback,
  onConnect,
}: ConnectGitLabDialogViewProps) {
  const [groupOrInstance, setGroupOrInstance] = React.useState("");
  const [instanceUrl, setInstanceUrl] = React.useState("");
  const [clientId, setClientId] = React.useState("");
  const [clientSecret, setClientSecret] = React.useState("");
  const [copied, setCopied] = React.useState(false);

  React.useEffect(() => {
    if (!open) {
      setGroupOrInstance("");
      setInstanceUrl("");
      setClientId("");
      setClientSecret("");
      setCopied(false);
    }
  }, [open]);

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
            Connect to GitLab
          </SheetTitle>
          <SheetDescription>
            GitLab doesn&apos;t support one-click App registration like GitHub, so we walk you
            through creating a Group OAuth Application by hand. Step 1: tell us your GitLab group +
            instance. Step 2: click the link to GitLab and paste the callback URL we generate. Step
            3: paste the resulting Application ID + Secret back here.
          </SheetDescription>
        </SheetHeader>

        <form onSubmit={submit} className="flex flex-1 flex-col gap-5 overflow-y-auto px-4 pb-4">
          {/* Step 1: group + instance */}
          <section className="space-y-3">
            <h3 className="text-sm font-semibold">
              <span className="text-muted-foreground mr-1.5">1.</span>
              Where does your GitLab live?
            </h3>
            <div className="space-y-2">
              <Label htmlFor="gl-group">GitLab group (optional)</Label>
              <Input
                id="gl-group"
                value={groupOrInstance}
                onChange={(e) => setGroupOrInstance(e.target.value)}
                placeholder="acme-corp (leave blank to use a user-level App)"
                className="font-mono text-xs"
                autoComplete="off"
              />
              <p className="text-muted-foreground text-xs">
                Group OAuth Apps are shared by everyone in the group; user-level Apps are tied to a
                single user.
              </p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="gl-instance">GitLab instance URL (self-hosted)</Label>
              <Input
                id="gl-instance"
                value={instanceUrl}
                onChange={(e) => setInstanceUrl(e.target.value)}
                placeholder="https://gitlab.acme.example (leave blank for SaaS gitlab.com)"
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
              Create the Application on GitLab
            </h3>
            <Button asChild variant="outline" className="w-full justify-start">
              <a href={gitlabAppsUrl} target="_blank" rel="noreferrer">
                <ExternalLinkIcon className="size-4" />
                Open GitLab Applications
                <span className="text-muted-foreground text-2xs ml-2 truncate font-mono">
                  {gitlabAppsUrl}
                </span>
              </a>
            </Button>
            <div className="space-y-1.5">
              <Label className="text-xs">Paste this Callback URL on GitLab</Label>
              <div className="flex items-center gap-2">
                <code className="bg-muted text-2xs flex-1 rounded px-2 py-1 font-mono break-all">
                  {callbackUrl}
                </code>
                <Button type="button" size="sm" variant="outline" onClick={onCopy}>
                  {copied ? <CheckIcon className="size-4" /> : <CopyIcon className="size-4" />}
                  {copied ? "Copied" : "Copy"}
                </Button>
              </div>
              <p className="text-muted-foreground text-xs">
                Required GitLab scopes: <code className="font-mono">read_api</code>,{" "}
                <code className="font-mono">read_repository</code>,{" "}
                <code className="font-mono">read_user</code>. Leave <em>Confidential</em> checked.
              </p>
            </div>
          </section>

          {/* Step 3: paste back */}
          <section className="space-y-3">
            <h3 className="text-sm font-semibold">
              <span className="text-muted-foreground mr-1.5">3.</span>
              Paste the result back here
            </h3>
            <div className="space-y-2">
              <Label htmlFor="gl-client-id">Application ID</Label>
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
              <Label htmlFor="gl-client-secret">Secret</Label>
              <Input
                id="gl-client-secret"
                type="password"
                value={clientSecret}
                onChange={(e) => setClientSecret(e.target.value)}
                className="font-mono text-xs"
                autoComplete="off"
                required
              />
              <p className="text-muted-foreground text-xs">
                Stored encrypted at rest. GitLab shows the Secret exactly once — copy it before
                clicking away.
              </p>
            </div>
          </section>

          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={loading || !clientId || !clientSecret}>
              {loading ? "Connecting…" : "Save & connect"}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
