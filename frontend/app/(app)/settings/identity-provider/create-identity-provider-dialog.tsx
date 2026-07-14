"use client";

import { useMutation } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

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
import { CREATE_IDENTITY_PROVIDER } from "@/graphql/identity/identity.mutations";
import { LIST_IDENTITY_PROVIDERS } from "@/graphql/identity/identity.queries";
import type {
  AstroliftIdentityProvider,
  IdpKind,
  MutationResult,
} from "@/graphql/identity/identity.types";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const KINDS: { value: IdpKind; label: string; hint: string }[] = [
  { value: "oidc", label: "Generic OIDC", hint: "discovery URL + client_id + client_secret_ref" },
  { value: "cognito", label: "Amazon Cognito", hint: "user pool + region + OIDC fields" },
  { value: "auth0", label: "Auth0", hint: "tenant URL + client_id + client_secret_ref" },
  { value: "okta", label: "Okta", hint: "discovery URL + client_id + client_secret_ref" },
  {
    value: "azure_ad",
    label: "Azure AD / Entra ID",
    hint: "discovery URL + client_id + client_secret_ref",
  },
  { value: "google", label: "Google Workspace", hint: "discovery URL + client_id" },
  { value: "github", label: "GitHub OAuth", hint: "OAuth app client_id + client_secret_ref" },
  { value: "saml", label: "SAML 2.0", hint: "metadata URL or XML upload" },
  {
    value: "local",
    label: "Local accounts",
    hint: "username + password (Django auth, dev/fallback)",
  },
];

const COGNITO_TEMPLATE = `{
  "user_pool_id": "",
  "region": ""
}`;

export function CreateIdentityProviderDialog({ open, onOpenChange }: Props) {
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

  const [createIdp, { loading }] = useMutation<{
    createIdentityProvider: MutationResult<AstroliftIdentityProvider>;
  }>(CREATE_IDENTITY_PROVIDER, {
    refetchQueries: [{ query: LIST_IDENTITY_PROVIDERS }],
    awaitRefetchQueries: true,
  });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    let parsedConfig: unknown = {};
    try {
      parsedConfig = JSON.parse(configText.trim() || "{}");
    } catch (err) {
      setError(err instanceof Error ? err.message : "invalid JSON");
      return;
    }

    const { data } = await createIdp({
      variables: {
        input: {
          kind,
          displayName: displayName.trim() || null,
          oidcDiscoveryUrl: oidcDiscoveryUrl.trim() || null,
          metadataUrl: metadataUrl.trim() || null,
          clientId: clientId.trim() || null,
          clientSecretRef: clientSecretRef.trim() || null,
          config: parsedConfig,
          setActive,
        },
      },
    });
    if (data?.createIdentityProvider.ok) {
      toast.success(`Configured ${data.createIdentityProvider.data?.name ?? kind}`);
      onOpenChange(false);
    } else {
      toast.error(data?.createIdentityProvider.errors?.[0]?.message ?? "Failed");
    }
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
          <SheetTitle>New identity provider</SheetTitle>
          <SheetDescription>
            Configure how users sign in. Multiple providers can coexist; one is active at a time.
            Secrets are stored as references into the platform secrets backend, never plaintext in
            this row.
          </SheetDescription>
        </SheetHeader>
        <form onSubmit={submit} className="flex flex-1 flex-col gap-4 overflow-y-auto px-4 pb-4">
          <div className="space-y-2">
            <Label htmlFor="kind">Kind</Label>
            <Select value={kind} onValueChange={(v) => setKind(v as IdpKind)}>
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
            <p className="text-muted-foreground text-xs">
              {KINDS.find((k) => k.value === kind)?.hint}
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="display-name">Display name (optional)</Label>
            <Input
              id="display-name"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="e.g. Auth0 prod, Cognito staging"
            />
            <p className="text-muted-foreground text-xs">
              Shown on the login screen and in audit logs. Defaults to the kind name when blank.
            </p>
          </div>

          {showOidc && (
            <>
              <div className="space-y-2">
                <Label htmlFor="discovery">OIDC discovery URL</Label>
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
                  <Label htmlFor="client-id">Client ID</Label>
                  <Input
                    id="client-id"
                    value={clientId}
                    onChange={(e) => setClientId(e.target.value)}
                    className="font-mono text-xs"
                    required
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="client-secret-ref">Secret reference</Label>
                  <Input
                    id="client-secret-ref"
                    value={clientSecretRef}
                    onChange={(e) => setClientSecretRef(e.target.value)}
                    placeholder="secrets-backend-path/oidc/client-secret"
                    className="font-mono text-xs"
                  />
                  <p className="text-muted-foreground text-2xs">
                    Path into the platform secrets backend; the value never lives in this row.
                  </p>
                </div>
              </div>
            </>
          )}

          {showSaml && (
            <div className="space-y-2">
              <Label htmlFor="metadata-url">SAML metadata URL</Label>
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
              <Label htmlFor="config">Kind-specific config (JSON)</Label>
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
                  Required keys for Cognito: <code>user_pool_id</code>, <code>region</code>.
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
            Make this the active provider for the organization
          </label>

          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={loading}>
              {loading ? "Saving…" : "Save provider"}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
