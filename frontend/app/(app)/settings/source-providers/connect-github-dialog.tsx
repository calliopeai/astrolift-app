"use client";

import { GithubIcon, ShieldCheckIcon } from "lucide-react";
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

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * "Connect to GitHub" — primary one-click flow.
 *
 * Operator names the GitHub org (or leaves blank for personal) and clicks
 * Continue. We bounce them through the backend's manifest-flow start
 * endpoint, which renders an auto-submitting form posting an
 * Astrolift-shaped App manifest to GitHub. GitHub creates the App,
 * walks the operator through naming/permission review, and redirects
 * back to Astrolift with a one-time code. The backend exchanges that
 * code for the App credentials, then bounces the operator to the
 * App's install page so they can pick which repos to grant access to.
 *
 * No client_id / client_secret / PEM paste required.
 */
export function ConnectGitHubDialog({ open, onOpenChange }: Props) {
  const [ghOrg, setGhOrg] = React.useState("");

  React.useEffect(() => {
    if (!open) setGhOrg("");
  }, [open]);

  const orgSlug = ghOrg.trim();
  // GitHub org/user slug rules: alphanumeric + hyphen, max 39 chars.
  // We re-validate server-side; this is just a UX hint.
  const looksValidOrg = orgSlug === "" || /^[A-Za-z0-9][A-Za-z0-9-]{0,38}$/.test(orgSlug);

  function startManifestFlow() {
    if (!looksValidOrg) return;
    const params = new URLSearchParams();
    if (orgSlug) params.set("org", orgSlug);
    params.set("return_to", "/settings/source-providers");
    // Full-page navigation — the backend renders a self-submitting
    // HTML form that POSTs to github.com on load.
    window.location.href = `/app/auth1/scm/github/app-manifest/start?${params.toString()}`;
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <GithubIcon className="size-5" />
            Connect to GitHub
          </SheetTitle>
          <SheetDescription>
            Astrolift will ask GitHub to create a private GitHub App on your account. GitHub will
            show you the App&apos;s name and exact permissions before anything is created. You can
            revoke the App from your GitHub settings at any time.
          </SheetDescription>
        </SheetHeader>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            startManifestFlow();
          }}
          className="flex flex-1 flex-col gap-4 overflow-y-auto px-4 pb-4"
        >
          <div className="space-y-2">
            <Label htmlFor="gh-org">Where should the App live?</Label>
            <Input
              id="gh-org"
              value={ghOrg}
              onChange={(e) => setGhOrg(e.target.value)}
              placeholder="acme-corp (leave blank for your personal account)"
              className="font-mono text-xs"
              autoComplete="off"
              autoFocus
            />
            <p className="text-muted-foreground text-xs">
              GitHub org slug. Leave blank to install on your personal account. You&apos;ll be able
              to switch the install target on the next screen if you change your mind.
            </p>
            {!looksValidOrg && (
              <p className="text-destructive text-xs">
                GitHub org slugs are alphanumeric + hyphen, max 39 characters.
              </p>
            )}
          </div>

          <div className="rounded-md border border-emerald-500/20 bg-emerald-500/5 p-3 text-xs text-emerald-900 dark:text-emerald-200">
            <p className="flex items-center gap-2 font-medium">
              <ShieldCheckIcon className="size-4" />
              Permissions Astrolift will request
            </p>
            <ul className="mt-2 space-y-1 pl-6 [&>li]:list-disc">
              <li>
                <span className="font-mono">contents: read</span> — clone your repos
              </li>
              <li>
                <span className="font-mono">metadata: read</span> — read repo metadata
              </li>
              <li>
                <span className="font-mono">pull_requests: write</span> — post deploy / preview
                comments on PRs
              </li>
              <li>
                <span className="font-mono">checks: write</span> — write commit statuses for deploys
              </li>
            </ul>
            <p className="mt-2">
              Plus push + pull_request webhooks. You can broaden later by editing the App on GitHub.
            </p>
          </div>

          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!looksValidOrg}>
              Continue on GitHub →
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
