"use client";

import { useTranslations } from "next-intl";
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
import { Textarea } from "@/components/ui/textarea";
import type { IdpKind } from "@/graphql/identity/identity.types";

import type { useCreateIdentityProvider } from "./use-create-identity-provider";

export type CreateIdentityProviderSheetProps = ReturnType<typeof useCreateIdentityProvider> & {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

// Labels and hints live under identityProviders.kinds / .hints.
const KINDS: IdpKind[] = [
  "oidc",
  "cognito",
  "auth0",
  "okta",
  "azure_ad",
  "google",
  "github",
  "saml",
  "local",
];

const COGNITO_TEMPLATE = `{
  "user_pool_id": "",
  "region": ""
}`;

export function CreateIdentityProviderSheet({
  open,
  onOpenChange,
  onCreate,
  creating,
}: CreateIdentityProviderSheetProps) {
  const t = useTranslations("identityProviders");
  const [kind, setKind] = React.useState<IdpKind>("oidc");
  const [displayName, setDisplayName] = React.useState("");
  const [oidcDiscoveryUrl, setOidcDiscoveryUrl] = React.useState("");
  const [metadataUrl, setMetadataUrl] = React.useState("");
  const [clientId, setClientId] = React.useState("");
  const [clientSecretRef, setClientSecretRef] = React.useState("");
  const [configText, setConfigText] = React.useState("{}");
  const [setActive, setSetActive] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!open) {
      setKind("oidc");
      setDisplayName("");
      setOidcDiscoveryUrl("");
      setMetadataUrl("");
      setClientId("");
      setClientSecretRef("");
      setConfigText("{}");
      setSetActive(false);
      setError(null);
    }
  }, [open]);

  // Suggest a config template when kind changes.
  React.useEffect(() => {
    if (kind === "cognito") setConfigText(COGNITO_TEMPLATE);
    else setConfigText("{}");
  }, [kind]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    let parsedConfig: unknown = {};
    try {
      parsedConfig = JSON.parse(configText.trim() || "{}");
    } catch (err) {
      setError(err instanceof Error ? err.message : t("create.invalidJson"));
      return;
    }

    const created = await onCreate({
      kind,
      displayName: displayName.trim() || null,
      oidcDiscoveryUrl: oidcDiscoveryUrl.trim() || null,
      metadataUrl: metadataUrl.trim() || null,
      clientId: clientId.trim() || null,
      clientSecretRef: clientSecretRef.trim() || null,
      config: parsedConfig,
      setActive,
    });
    if (created) onOpenChange(false);
  }

  const showOidc = ["oidc", "cognito", "auth0", "okta", "azure_ad", "google", "github"].includes(
    kind
  );
  const showSaml = kind === "saml";
  const isLocal = kind === "local";

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle>{t("create.title")}</SheetTitle>
          <SheetDescription>{t("create.description")}</SheetDescription>
        </SheetHeader>
        <form onSubmit={submit} className="flex flex-1 flex-col gap-4 overflow-y-auto px-4 pb-4">
          <div className="space-y-2">
            <Label htmlFor="kind">{t("create.kind")}</Label>
            <Select value={kind} onValueChange={(v) => setKind(v as IdpKind)}>
              <SelectTrigger id="kind">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {KINDS.map((k) => (
                  <SelectItem key={k} value={k}>
                    {t(`kinds.${k}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-muted-foreground text-xs">{t(`hints.${kind}`)}</p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="display-name">{t("create.displayName")}</Label>
            <Input
              id="display-name"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder={t("create.displayNamePlaceholder")}
            />
            <p className="text-muted-foreground text-xs">{t("create.displayNameHelp")}</p>
          </div>

          {showOidc && (
            <>
              <div className="space-y-2">
                <Label htmlFor="discovery">{t("create.discovery")}</Label>
                <Input
                  id="discovery"
                  value={oidcDiscoveryUrl}
                  onChange={(e) => setOidcDiscoveryUrl(e.target.value)}
                  type="url"
                  placeholder="https://example.auth0.com/.well-known/openid-configuration"
                  className="font-mono text-xs"
                />
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label htmlFor="client-id">{t("create.clientId")}</Label>
                  <Input
                    id="client-id"
                    value={clientId}
                    onChange={(e) => setClientId(e.target.value)}
                    className="font-mono text-xs"
                    required
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="client-secret-ref">{t("create.secretRef")}</Label>
                  <Input
                    id="client-secret-ref"
                    value={clientSecretRef}
                    onChange={(e) => setClientSecretRef(e.target.value)}
                    placeholder="secrets-backend-path/oidc/client-secret"
                    className="font-mono text-xs"
                  />
                  <p className="text-muted-foreground text-2xs">{t("create.secretRefHelp")}</p>
                </div>
              </div>
            </>
          )}

          {showSaml && (
            <div className="space-y-2">
              <Label htmlFor="metadata-url">{t("create.metadataUrl")}</Label>
              <Input
                id="metadata-url"
                value={metadataUrl}
                onChange={(e) => setMetadataUrl(e.target.value)}
                type="url"
                placeholder="https://idp.example.com/metadata"
                className="font-mono text-xs"
                required
              />
            </div>
          )}

          {!isLocal && (
            <div className="space-y-2">
              <Label htmlFor="config">{t("create.config")}</Label>
              <Textarea
                id="config"
                value={configText}
                onChange={(e) => setConfigText(e.target.value)}
                rows={kind === "cognito" ? 5 : 4}
                className="font-mono text-xs"
              />
              {error && <p className="text-destructive text-xs">{error}</p>}
              {kind === "cognito" && (
                <p className="text-muted-foreground text-xs">
                  {t.rich("create.cognitoKeys", { code: (chunks) => <code>{chunks}</code> })}
                </p>
              )}
            </div>
          )}

          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={setActive}
              onChange={(e) => setSetActive(e.target.checked)}
            />
            {t("create.setActive")}
          </label>

          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {t("create.cancel")}
            </Button>
            <Button type="submit" disabled={creating}>
              {creating ? t("create.saving") : t("create.save")}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
