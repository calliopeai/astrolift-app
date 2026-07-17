"use client";

import { useMutation } from "@apollo/client/react";
import { GithubIcon, KeyRoundIcon, PlusCircleIcon, ShieldCheckIcon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

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
import { Textarea } from "@/components/ui/textarea";
import { CONNECT_EXISTING_GITHUB_APP } from "@/graphql/scm/scm.mutations";
import { LIST_SOURCE_CONNECTIONS } from "@/graphql/scm/scm.queries";
import type { AstroliftSourceConnection, MutationResult } from "@/graphql/scm/scm.types";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** Which connect method the operator is looking at. `choose` is the picker. */
type Method = "choose" | "bootstrap" | "byo";

// GitHub App Client ID shape — mirrors the backend regex in
// astrolift_scm/schema/mutations.py so paste errors surface before the
// round-trip. Empty is allowed (Client ID is optional for the BYO flow).
const CLIENT_ID_NEW = /^Iv\d+[A-Za-z0-9]+$/;
const CLIENT_ID_LEGACY = /^[a-f0-9]{20}$/;
function looksLikeClientId(value: string): boolean {
  const v = value.trim();
  return v === "" || CLIENT_ID_NEW.test(v) || CLIENT_ID_LEGACY.test(v);
}

interface ConnectExistingResp {
  connectExistingGithubApp: MutationResult<
    Pick<AstroliftSourceConnection, "id" | "name" | "accountLogin" | "installationId">
  >;
}

/**
 * "Connect to GitHub" — offers BOTH connect methods:
 *
 *  - **Bootstrap**: ask GitHub to CREATE a new App via the manifest flow
 *    (auth1.scm_app_manifest). One click, no secret paste. Unchanged.
 *  - **BYO**: adopt an App the operator ALREADY created on GitHub, by
 *    pasting its App ID + private-key PEM. Runs the
 *    ``connectExistingGithubApp`` mutation, which validates the creds
 *    end-to-end (discovers the installation, mints a token) before storing.
 *
 * Secrets are never echoed back: on success we clear the form and close.
 */
export function ConnectGitHubDialog({ open, onOpenChange }: Props) {
  const [method, setMethod] = React.useState<Method>("choose");

  // ---- Bootstrap (manifest create) state --------------------------------
  const [ghOrg, setGhOrg] = React.useState("");

  // ---- BYO (adopt existing) state ---------------------------------------
  const [appId, setAppId] = React.useState("");
  const [pem, setPem] = React.useState("");
  const [clientId, setClientId] = React.useState("");
  const [clientSecret, setClientSecret] = React.useState("");
  const [webhookSecret, setWebhookSecret] = React.useState("");
  const [orgLogin, setOrgLogin] = React.useState("");
  const [apiBaseUrl, setApiBaseUrl] = React.useState("");

  // Reset every field on close so secrets never linger in component state
  // and the picker starts fresh next open. Done in the close handler (not an
  // effect) so there's no synchronous setState-in-effect cascade.
  function handleOpenChange(next: boolean) {
    if (!next) {
      setMethod("choose");
      setGhOrg("");
      setAppId("");
      setPem("");
      setClientId("");
      setClientSecret("");
      setWebhookSecret("");
      setOrgLogin("");
      setApiBaseUrl("");
    }
    onOpenChange(next);
  }

  const [connectExisting, { loading }] = useMutation<ConnectExistingResp>(
    CONNECT_EXISTING_GITHUB_APP,
    {
      refetchQueries: [{ query: LIST_SOURCE_CONNECTIONS }],
      awaitRefetchQueries: true,
    },
  );

  // ---- Bootstrap validation + launch ------------------------------------
  const orgSlug = ghOrg.trim();
  const looksValidOrg = orgSlug === "" || /^[A-Za-z0-9][A-Za-z0-9-]{0,38}$/.test(orgSlug);

  function startManifestFlow() {
    if (!looksValidOrg) return;
    const params = new URLSearchParams();
    if (orgSlug) params.set("org", orgSlug);
    params.set("return_to", "/providers#source");
    window.location.href = `/app/auth1/scm/github/app-manifest/start?${params.toString()}`;
  }

  // ---- BYO validation + submit ------------------------------------------
  const appIdOk = /^\d+$/.test(appId.trim());
  const clientIdOk = looksLikeClientId(clientId);
  const canSubmitByo = appIdOk && pem.trim() !== "" && clientIdOk && !loading;

  async function submitByo(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmitByo) return;
    const { data } = await connectExisting({
      variables: {
        input: {
          appId: appId.trim(),
          privateKeyPem: pem,
          clientId: clientId.trim() || null,
          clientSecret: clientSecret || null,
          webhookSecret: webhookSecret || null,
          orgLogin: orgLogin.trim() || null,
          apiBaseUrl: apiBaseUrl.trim() || null,
        },
      },
    });
    const res = data?.connectExistingGithubApp;
    if (res?.ok) {
      toast.success(`Connected GitHub App: ${res.data?.name ?? appId.trim()}`);
      handleOpenChange(false);
    } else {
      toast.error(res?.errors?.[0]?.message ?? "Couldn't connect the GitHub App");
    }
  }

  return (
    <Sheet open={open} onOpenChange={handleOpenChange}>
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <GithubIcon className="size-5" />
            {method === "byo" ? "Connect an existing GitHub App" : "Connect to GitHub"}
          </SheetTitle>
          <SheetDescription>
            {method === "byo"
              ? "Adopt a GitHub App you already created. Paste its App ID and private key — Astrolift verifies the credentials against GitHub before saving them."
              : "Create a brand-new App on your account in one click, or connect an App you already made."}
          </SheetDescription>
        </SheetHeader>

        {/* ---- Method picker ---- */}
        {method === "choose" && (
          <div className="flex flex-1 flex-col gap-3 overflow-y-auto px-4 pb-4">
            <button
              type="button"
              onClick={() => setMethod("bootstrap")}
              className="hover:border-primary hover:bg-accent/40 flex items-start gap-3 rounded-md border p-4 text-left transition"
            >
              <PlusCircleIcon className="text-primary mt-0.5 size-5 shrink-0" />
              <span className="space-y-1">
                <span className="block font-medium">Create a new GitHub App</span>
                <span className="text-muted-foreground block text-xs">
                  Recommended. Astrolift asks GitHub to register a private App on your account —
                  no client_id / secret / PEM to paste. GitHub shows you the name and permissions
                  first.
                </span>
              </span>
            </button>
            <button
              type="button"
              onClick={() => setMethod("byo")}
              className="hover:border-primary hover:bg-accent/40 flex items-start gap-3 rounded-md border p-4 text-left transition"
            >
              <KeyRoundIcon className="text-primary mt-0.5 size-5 shrink-0" />
              <span className="space-y-1">
                <span className="block font-medium">Connect an existing GitHub App</span>
                <span className="text-muted-foreground block text-xs">
                  Bring your own App — one you already created on GitHub. Paste its App ID and
                  private-key PEM. Good for GitHub Enterprise or an App shared across tools.
                </span>
              </span>
            </button>
          </div>
        )}

        {/* ---- Bootstrap (create a new App) ---- */}
        {method === "bootstrap" && (
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

            <div className="rounded-md border border-success-border bg-success/5 p-3 text-xs text-success-fg">
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

            <SheetFooter className="mt-auto flex-row justify-between gap-2 px-0">
              <Button type="button" variant="ghost" onClick={() => setMethod("choose")}>
                ← Back
              </Button>
              <Button type="submit" disabled={!looksValidOrg}>
                Continue on GitHub →
              </Button>
            </SheetFooter>
          </form>
        )}

        {/* ---- BYO (adopt an existing App) ---- */}
        {method === "byo" && (
          <form onSubmit={submitByo} className="flex flex-1 flex-col gap-4 overflow-y-auto px-4 pb-4">
            <div className="space-y-2">
              <Label htmlFor="byo-app-id">App ID</Label>
              <Input
                id="byo-app-id"
                value={appId}
                onChange={(e) => setAppId(e.target.value)}
                placeholder="3705068"
                className="font-mono text-xs"
                autoComplete="off"
                inputMode="numeric"
                autoFocus
                required
              />
              <p className="text-muted-foreground text-xs">
                The numeric App ID from your GitHub App&apos;s settings page (not the Client ID).
              </p>
              {appId.trim() !== "" && !appIdOk && (
                <p className="text-destructive text-xs">The App ID is the numeric id, e.g. 3705068.</p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="byo-pem">Private key (PEM)</Label>
              <Textarea
                id="byo-pem"
                value={pem}
                onChange={(e) => setPem(e.target.value)}
                placeholder={"-----BEGIN RSA PRIVATE KEY-----\n…\n-----END RSA PRIVATE KEY-----"}
                className="h-32 font-mono text-xs"
                autoComplete="off"
                spellCheck={false}
                required
              />
              <p className="text-muted-foreground text-xs">
                Generate a private key on your GitHub App page and paste the whole{" "}
                <code className="font-mono">.pem</code> here. It is encrypted at rest and never
                shown again.
              </p>
            </div>

            <div className="space-y-2">
              <Label htmlFor="byo-client-id">
                Client ID <span className="text-muted-foreground">(optional)</span>
              </Label>
              <Input
                id="byo-client-id"
                value={clientId}
                onChange={(e) => setClientId(e.target.value)}
                placeholder="Iv23lic8662KXwe4XKEI"
                className="font-mono text-xs"
                autoComplete="off"
              />
              <p className="text-muted-foreground text-xs">
                Needed only for the user-to-server &quot;Connect my GitHub&quot; dance.
              </p>
              {!clientIdOk && (
                <p className="text-destructive text-xs">
                  Doesn&apos;t look like a GitHub App Client ID — expected <code>Iv…</code> or a
                  20-char hex string.
                </p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="byo-client-secret">
                Client secret <span className="text-muted-foreground">(optional)</span>
              </Label>
              <Input
                id="byo-client-secret"
                type="password"
                value={clientSecret}
                onChange={(e) => setClientSecret(e.target.value)}
                className="font-mono text-xs"
                autoComplete="new-password"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="byo-webhook-secret">
                Webhook secret <span className="text-muted-foreground">(optional)</span>
              </Label>
              <Input
                id="byo-webhook-secret"
                type="password"
                value={webhookSecret}
                onChange={(e) => setWebhookSecret(e.target.value)}
                className="font-mono text-xs"
                autoComplete="new-password"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="byo-org-login">
                GitHub org / user login <span className="text-muted-foreground">(optional)</span>
              </Label>
              <Input
                id="byo-org-login"
                value={orgLogin}
                onChange={(e) => setOrgLogin(e.target.value)}
                placeholder="acme-corp"
                className="font-mono text-xs"
                autoComplete="off"
              />
              <p className="text-muted-foreground text-xs">
                Only needed if the App is installed on more than one account — Astrolift uses it to
                pick the right installation.
              </p>
            </div>

            <div className="space-y-2">
              <Label htmlFor="byo-api-base">
                API base URL <span className="text-muted-foreground">(optional)</span>
              </Label>
              <Input
                id="byo-api-base"
                value={apiBaseUrl}
                onChange={(e) => setApiBaseUrl(e.target.value)}
                placeholder="https://github.example.com/api/v3"
                className="font-mono text-xs"
                autoComplete="off"
              />
              <p className="text-muted-foreground text-xs">
                For GitHub Enterprise Server. Leave blank for github.com.
              </p>
            </div>

            <SheetFooter className="mt-auto flex-row justify-between gap-2 px-0">
              <Button type="button" variant="ghost" onClick={() => setMethod("choose")}>
                ← Back
              </Button>
              <Button type="submit" disabled={!canSubmitByo}>
                {loading ? "Verifying…" : "Connect App"}
              </Button>
            </SheetFooter>
          </form>
        )}
      </SheetContent>
    </Sheet>
  );
}
