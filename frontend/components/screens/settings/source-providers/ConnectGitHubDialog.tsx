"use client";

import { GithubIcon, KeyRoundIcon, PlusCircleIcon, ShieldCheckIcon } from "lucide-react";
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
import { Textarea } from "@/components/ui/textarea";

import type { useConnectGitHub } from "./use-connect-github";

export type ConnectGitHubDialogViewProps = ReturnType<typeof useConnectGitHub> & {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

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
export function ConnectGitHubDialogView({
  open,
  onOpenChange,
  loading,
  startManifestFlow,
  onConnectExisting,
}: ConnectGitHubDialogViewProps) {
  const t = useTranslations("sourceProviders.github");
  const tConnect = useTranslations("sourceProviders.connect");
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

  // ---- Bootstrap validation + launch ------------------------------------
  const orgSlug = ghOrg.trim();
  const looksValidOrg = orgSlug === "" || /^[A-Za-z0-9][A-Za-z0-9-]{0,38}$/.test(orgSlug);

  function startBootstrap() {
    if (!looksValidOrg) return;
    startManifestFlow(orgSlug);
  }

  // ---- BYO validation + submit ------------------------------------------
  const appIdOk = /^\d+$/.test(appId.trim());
  const clientIdOk = looksLikeClientId(clientId);
  const canSubmitByo = appIdOk && pem.trim() !== "" && clientIdOk && !loading;

  async function submitByo(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmitByo) return;
    const ok = await onConnectExisting({
      appId: appId.trim(),
      privateKeyPem: pem,
      clientId: clientId.trim() || null,
      clientSecret: clientSecret || null,
      webhookSecret: webhookSecret || null,
      orgLogin: orgLogin.trim() || null,
      apiBaseUrl: apiBaseUrl.trim() || null,
    });
    if (ok) handleOpenChange(false);
  }

  return (
    <Sheet open={open} onOpenChange={handleOpenChange}>
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <GithubIcon className="size-5" />
            {method === "byo" ? t("titleByo") : t("title")}
          </SheetTitle>
          <SheetDescription>
            {method === "byo" ? t("descriptionByo") : t("description")}
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
                <span className="block font-medium">{t("createTitle")}</span>
                <span className="text-muted-foreground block text-xs">{t("createBody")}</span>
              </span>
            </button>
            <button
              type="button"
              onClick={() => setMethod("byo")}
              className="hover:border-primary hover:bg-accent/40 flex items-start gap-3 rounded-md border p-4 text-left transition"
            >
              <KeyRoundIcon className="text-primary mt-0.5 size-5 shrink-0" />
              <span className="space-y-1">
                <span className="block font-medium">{t("byoTitle")}</span>
                <span className="text-muted-foreground block text-xs">{t("byoBody")}</span>
              </span>
            </button>
          </div>
        )}

        {/* ---- Bootstrap (create a new App) ---- */}
        {method === "bootstrap" && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              startBootstrap();
            }}
            className="flex flex-1 flex-col gap-4 overflow-y-auto px-4 pb-4"
          >
            <div className="space-y-2">
              <Label htmlFor="gh-org">{t("orgLabel")}</Label>
              <Input
                id="gh-org"
                value={ghOrg}
                onChange={(e) => setGhOrg(e.target.value)}
                placeholder={t("orgPlaceholder")}
                className="font-mono text-xs"
                autoComplete="off"
                autoFocus
              />
              <p className="text-muted-foreground text-xs">{t("orgHelp")}</p>
              {!looksValidOrg && <p className="text-destructive text-xs">{t("orgInvalid")}</p>}
            </div>

            <div className="border-success-border bg-success/5 text-success-fg rounded-md border p-3 text-xs">
              <p className="flex items-center gap-2 font-medium">
                <ShieldCheckIcon className="size-4" />
                {t("permsTitle")}
              </p>
              <ul className="mt-2 space-y-1 pl-6 [&>li]:list-disc">
                <li>
                  <span className="font-mono">contents: read</span> ({t("perms.contents")})
                </li>
                <li>
                  <span className="font-mono">metadata: read</span> ({t("perms.metadata")})
                </li>
                <li>
                  <span className="font-mono">pull_requests: write</span> ({t("perms.pullRequests")}
                  )
                </li>
                <li>
                  <span className="font-mono">checks: write</span> ({t("perms.checks")})
                </li>
              </ul>
              <p className="mt-2">{t("permsFooter")}</p>
            </div>

            <SheetFooter className="mt-auto flex-row justify-between gap-2 px-0">
              <Button type="button" variant="ghost" onClick={() => setMethod("choose")}>
                {t("back")}
              </Button>
              <Button type="submit" disabled={!looksValidOrg}>
                {t("continue")}
              </Button>
            </SheetFooter>
          </form>
        )}

        {/* ---- BYO (adopt an existing App) ---- */}
        {method === "byo" && (
          <form
            onSubmit={submitByo}
            className="flex flex-1 flex-col gap-4 overflow-y-auto px-4 pb-4"
          >
            <div className="space-y-2">
              <Label htmlFor="byo-app-id">{t("appId")}</Label>
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
              <p className="text-muted-foreground text-xs">{t("appIdHelp")}</p>
              {appId.trim() !== "" && !appIdOk && (
                <p className="text-destructive text-xs">{t("appIdInvalid")}</p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="byo-pem">{t("pem")}</Label>
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
                {t.rich("pemHelp", {
                  code: (chunks) => <code className="font-mono">{chunks}</code>,
                })}
              </p>
            </div>

            <div className="space-y-2">
              <Label htmlFor="byo-client-id">
                {t("clientId")} <span className="text-muted-foreground">{t("optional")}</span>
              </Label>
              <Input
                id="byo-client-id"
                value={clientId}
                onChange={(e) => setClientId(e.target.value)}
                placeholder="Iv23lic8662KXwe4XKEI"
                className="font-mono text-xs"
                autoComplete="off"
              />
              <p className="text-muted-foreground text-xs">{t("clientIdHelp")}</p>
              {!clientIdOk && (
                <p className="text-destructive text-xs">
                  {tConnect.rich("appClientIdInvalid", { code: (chunks) => <code>{chunks}</code> })}
                </p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="byo-client-secret">
                {t("clientSecret")} <span className="text-muted-foreground">{t("optional")}</span>
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
                {t("webhookSecret")} <span className="text-muted-foreground">{t("optional")}</span>
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
                {t("orgLogin")} <span className="text-muted-foreground">{t("optional")}</span>
              </Label>
              <Input
                id="byo-org-login"
                value={orgLogin}
                onChange={(e) => setOrgLogin(e.target.value)}
                placeholder="acme-corp"
                className="font-mono text-xs"
                autoComplete="off"
              />
              <p className="text-muted-foreground text-xs">{t("orgLoginHelp")}</p>
            </div>

            <div className="space-y-2">
              <Label htmlFor="byo-api-base">
                {t("apiBaseUrl")} <span className="text-muted-foreground">{t("optional")}</span>
              </Label>
              <Input
                id="byo-api-base"
                value={apiBaseUrl}
                onChange={(e) => setApiBaseUrl(e.target.value)}
                placeholder="https://github.example.com/api/v3"
                className="font-mono text-xs"
                autoComplete="off"
              />
              <p className="text-muted-foreground text-xs">{t("apiBaseUrlHelp")}</p>
            </div>

            <SheetFooter className="mt-auto flex-row justify-between gap-2 px-0">
              <Button type="button" variant="ghost" onClick={() => setMethod("choose")}>
                {t("back")}
              </Button>
              <Button type="submit" disabled={!canSubmitByo}>
                {loading ? t("verifying") : t("connectApp")}
              </Button>
            </SheetFooter>
          </form>
        )}
      </SheetContent>
    </Sheet>
  );
}
