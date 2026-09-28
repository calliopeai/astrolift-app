"use client";

import {
  AlertTriangleIcon,
  KeyRoundIcon,
  Loader2Icon,
  PencilIcon,
  RocketIcon,
  ShieldIcon,
} from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
} from "@/components/ui/combobox";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

import { Field } from "./Field";
import {
  type CognitoUserPool,
  type CognitoUserPoolClient,
  type IngressAuthConfig,
  poolIdFromArn,
} from "./types";
import type { useIngressAuth } from "./use-ingress-auth";

export type IngressAuthViewProps = ReturnType<typeof useIngressAuth>;

/**
 * Accessible on/off switch. The repo has no shadcn/radix Switch
 * primitive, so this is a small local toggle styled with the same
 * Tailwind tokens the rest of the settings surface uses — not a new
 * shared component. The "on" track is emerald so the enabled state
 * reads as a security control rather than a neutral preference.
 */
function AuthGateToggle({
  checked,
  onChange,
  disabled,
  label,
}: {
  checked: boolean;
  onChange: () => void;
  disabled?: boolean;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={onChange}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors",
        "focus-visible:ring-ring focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none",
        "disabled:cursor-not-allowed disabled:opacity-50",
        checked ? "bg-success" : "bg-muted-foreground/30"
      )}
    >
      <span
        className={cn(
          "inline-block size-4 rounded-full bg-white shadow transition-transform",
          checked ? "translate-x-4" : "translate-x-0.5"
        )}
      />
    </button>
  );
}

/** Ingress auth gate card (#851) with the Cognito pickers (#859). */
export function IngressAuthView({
  providerPluginSlug,
  ingressClass,
  existing,
  isAws,
  editing,
  poolId,
  onPoolIdChange,
  pools,
  poolsLoading,
  poolsErrored,
  clients,
  clientsLoading,
  busy,
  reconciling,
  onOpenForm,
  onCancelEdit,
  onDisable,
  onApply,
  onSaveAndApply,
}: IngressAuthViewProps) {
  const enabled = existing !== null;

  const [poolArn, setPoolArn] = React.useState(existing?.user_pool_arn ?? "");
  const [clientId, setClientId] = React.useState(existing?.user_pool_client_id ?? "");
  const [domain, setDomain] = React.useState(existing?.user_pool_domain ?? "");
  // "Advanced / paste directly" fallback. AWS-only pickers degrade to
  // the original free-text inputs when the operator wants to paste a
  // cross-account pool the cluster's IAM role can't enumerate, or when
  // the Cognito list query errors.
  const [useAdvanced, setUseAdvanced] = React.useState(false);

  function configFromFields(): IngressAuthConfig | null {
    if (poolArn && clientId && domain) {
      return { user_pool_arn: poolArn, user_pool_client_id: clientId, user_pool_domain: domain };
    }
    return null;
  }

  function openForm() {
    setPoolArn(existing?.user_pool_arn ?? "");
    setClientId(existing?.user_pool_client_id ?? "");
    setDomain(existing?.user_pool_domain ?? "");
    setUseAdvanced(false);
    onOpenForm();
  }

  // Operator picked a pool from the combobox: fill the ARN + pool id,
  // auto-fill the hosted domain (the whole point of #859 — no more
  // hand-typing it), and reset the app-client selection so the
  // dependent picker re-queries against the new pool.
  function pickPool(pool: CognitoUserPool) {
    setPoolArn(pool.poolArn);
    onPoolIdChange(pool.poolId);
    if (pool.domain) {
      setDomain(pool.domain);
    }
    setClientId("");
  }

  async function handleToggle() {
    if (busy) return;
    if (enabled) {
      await onDisable();
    } else {
      // Can't enable without config — open the form to collect it.
      openForm();
    }
  }

  async function handleApply() {
    if (busy) return;
    await onApply();
  }

  async function handleSaveAndApply() {
    if (busy) return;
    await onSaveAndApply(configFromFields());
  }

  // Per-provider auth metadata. AWS+ALB is the only fully-supported path
  // today; other providers show a "coming soon" state so the card is honest
  // rather than showing AWS-specific copy on a GKE or AKS cluster.
  const providerAuthMeta: Record<
    string,
    { supported: boolean; label: string; description: string; comingSoon?: string }
  > = {
    aws: {
      supported: ingressClass === "alb",
      label: "Cognito auth gate",
      description:
        "AWS ALB authenticate-cognito — applied to every managed-subdomain Ingress on this cluster.",
      comingSoon:
        ingressClass !== "alb" ? "ALB Cognito auth requires ingressClass = alb." : undefined,
    },
    gcp: {
      supported: false,
      label: "Google IAP",
      description: "Google Identity-Aware Proxy — per-app OAuth gate on GKE Ingress rules.",
      comingSoon: "Google IAP auth gate is not yet supported.",
    },
    azure: {
      supported: false,
      label: "Azure AD",
      description: "Azure Active Directory — per-app auth gate on AKS Application Gateway Ingress.",
      comingSoon: "Azure AD auth gate is not yet supported.",
    },
    k8s_native: {
      supported: false,
      label: "OIDC (oauth2-proxy)",
      description: "Generic OIDC via oauth2-proxy — Dex or any OIDC-compliant IdP.",
      comingSoon: "OIDC auth gate for raw k8s clusters is tracked in #852.",
    },
  };

  const authMeta = providerAuthMeta[providerPluginSlug] ?? {
    supported: false,
    label: "Auth gate",
    description: "Ingress-level auth gate.",
    comingSoon: `Auth gate is not yet supported for provider ${providerPluginSlug}.`,
  };

  if (!authMeta.supported) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <KeyRoundIcon className="size-4" />
            {authMeta.label}
          </CardTitle>
          <CardDescription className="mt-1">{authMeta.description}</CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground text-sm">{authMeta.comingSoon}</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              {enabled ? (
                <ShieldIcon className="text-success-fg size-4" />
              ) : (
                <KeyRoundIcon className="size-4" />
              )}
              {authMeta.label}
            </CardTitle>
            <CardDescription className="mt-1">{authMeta.description}</CardDescription>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <AuthGateToggle
              checked={enabled}
              onChange={handleToggle}
              disabled={busy}
              label={enabled ? "Disable auth gate" : "Enable auth gate"}
            />
            <span
              className={cn(
                "text-xs font-medium",
                enabled ? "text-success-fg" : "text-muted-foreground"
              )}
            >
              {enabled ? "Enabled" : "Disabled"}
            </span>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {editing ? (
          <div className="space-y-3">
            {isAws && !useAdvanced ? (
              <>
                <div className="space-y-1">
                  <label className="text-xs font-medium">User pool</label>
                  <CognitoPoolCombobox
                    pools={pools}
                    loading={poolsLoading}
                    errored={poolsErrored}
                    poolArn={poolArn}
                    onPick={pickPool}
                    onFreeText={(v) => {
                      setPoolArn(v);
                      onPoolIdChange(poolIdFromArn(v));
                    }}
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium">App client</label>
                  <CognitoClientCombobox
                    clients={clients}
                    loading={clientsLoading}
                    disabled={!poolId}
                    clientId={clientId}
                    onPick={setClientId}
                    onFreeText={setClientId}
                  />
                  {!poolId && (
                    <p className="text-muted-foreground text-xs">Pick a user pool first.</p>
                  )}
                </div>
              </>
            ) : (
              <>
                <div className="space-y-1">
                  <label className="text-xs font-medium">User pool ARN</label>
                  <Input
                    value={poolArn}
                    onChange={(e) => {
                      setPoolArn(e.target.value);
                      onPoolIdChange(poolIdFromArn(e.target.value));
                    }}
                    placeholder="arn:aws:cognito-idp:us-west-2:…"
                    className="font-mono text-xs"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium">App client ID</label>
                  <Input
                    value={clientId}
                    onChange={(e) => setClientId(e.target.value)}
                    placeholder="abc123…"
                    className="font-mono text-xs"
                  />
                </div>
              </>
            )}
            <div className="space-y-1">
              <label className="text-xs font-medium">User pool domain</label>
              <Input
                value={domain}
                onChange={(e) => setDomain(e.target.value)}
                placeholder="my-domain (without .auth.region.amazoncognito.com)"
                className="font-mono text-xs"
              />
              {isAws && !useAdvanced && (
                <p className="text-muted-foreground text-xs">
                  Auto-filled from the selected pool; edit to override.
                </p>
              )}
            </div>
            {isAws && (
              <button
                type="button"
                onClick={() => setUseAdvanced((v) => !v)}
                className="text-muted-foreground hover:text-foreground text-xs underline-offset-4 hover:underline"
              >
                {useAdvanced
                  ? "Use the pool picker"
                  : "Advanced: paste ARN / client ID directly (cross-account pools)"}
              </button>
            )}
            <div className="flex items-center gap-2 pt-1">
              <Button size="sm" onClick={handleSaveAndApply} disabled={busy} className="gap-1.5">
                {busy && <Loader2Icon className="size-3.5 animate-spin" />}
                Save &amp; Apply
              </Button>
              <Button size="sm" variant="ghost" onClick={onCancelEdit} disabled={busy}>
                Cancel
              </Button>
            </div>
          </div>
        ) : enabled ? (
          <div className="space-y-3">
            <div className="space-y-2 text-sm">
              <Field label="User pool ARN" mono value={existing.user_pool_arn} />
              <Field label="App client ID" mono value={existing.user_pool_client_id} />
              <Field label="Domain" mono value={existing.user_pool_domain} />
            </div>
            <div className="flex items-center gap-2 pt-1">
              <Button size="sm" onClick={handleApply} disabled={busy} className="gap-1.5">
                {reconciling && <Loader2Icon className="size-3.5 animate-spin" />}
                <RocketIcon className="size-3.5" />
                Apply to cluster
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={openForm}
                disabled={busy}
                className="gap-1.5"
              >
                <PencilIcon className="size-3.5" />
                Edit
              </Button>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="border-warning-border bg-warning/10 flex items-start gap-2 rounded-md border p-3">
              <AlertTriangleIcon className="text-warning-fg mt-0.5 size-4 shrink-0" />
              <p className="text-warning-fg text-sm">
                All apps on this cluster are publicly accessible. Enable the auth gate to put every
                managed-subdomain Ingress behind {authMeta.label}.
              </p>
            </div>
            <Button size="sm" onClick={openForm} disabled={busy} className="gap-1.5">
              <ShieldIcon className="size-3.5" />
              Enable
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// User-pool picker (#859) — searchable combobox over the cluster's
// Cognito pools with free-text fallback. The displayed value is the
// pool ARN; picking a pool fills the ARN + pool id + auto-fills the
// domain (via onPick), while typing commits a raw ARN (via onFreeText)
// for cross-account pools the cluster's IAM role can't enumerate.
function CognitoPoolCombobox({
  pools,
  loading,
  errored,
  poolArn,
  onPick,
  onFreeText,
}: {
  pools: CognitoUserPool[];
  loading: boolean;
  errored: boolean;
  poolArn: string;
  onPick: (pool: CognitoUserPool) => void;
  onFreeText: (v: string) => void;
}) {
  const selected = pools.find((p) => p.poolArn === poolArn) ?? null;
  return (
    <>
      <Combobox<CognitoUserPool>
        items={pools}
        itemToStringLabel={(p) => p.poolArn}
        value={selected}
        onValueChange={(v) => {
          if (v && typeof v === "object" && "poolArn" in v) {
            onPick(v);
          }
        }}
        inputValue={poolArn}
        onInputValueChange={(v) => onFreeText(v ?? "")}
      >
        <ComboboxInput
          placeholder={loading ? "Loading pools…" : "arn:aws:cognito-idp:us-west-2:…"}
          className="font-mono text-xs"
        />
        <ComboboxContent>
          <ComboboxEmpty>
            {poolArn ? `Use "${poolArn}" (paste ARN directly)` : "No pools found — paste an ARN."}
          </ComboboxEmpty>
          <ComboboxList>
            {(item: CognitoUserPool) => (
              <ComboboxItem key={item.poolId} value={item}>
                <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <span className="truncate text-sm">{item.name || item.poolId}</span>
                  <span className="text-muted-foreground text-2xs truncate font-mono">
                    {item.poolId}
                    {item.domain ? ` · ${item.domain}` : ""}
                  </span>
                </div>
              </ComboboxItem>
            )}
          </ComboboxList>
        </ComboboxContent>
      </Combobox>
      {errored && (
        <p className="text-muted-foreground text-xs">
          Couldn&apos;t list pools (the cluster&apos;s role may lack cognito-idp:ListUserPools).
          Paste the ARN directly.
        </p>
      )}
    </>
  );
}

// Dependent app-client picker (#859) — enabled once a pool is selected.
// Same free-text fallback shape as the pool picker.
function CognitoClientCombobox({
  clients,
  loading,
  disabled,
  clientId,
  onPick,
  onFreeText,
}: {
  clients: CognitoUserPoolClient[];
  loading: boolean;
  disabled: boolean;
  clientId: string;
  onPick: (v: string) => void;
  onFreeText: (v: string) => void;
}) {
  const selected = clients.find((c) => c.clientId === clientId) ?? null;
  return (
    <Combobox<CognitoUserPoolClient>
      items={clients}
      itemToStringLabel={(c) => c.clientId}
      value={selected}
      onValueChange={(v) => {
        if (v && typeof v === "object" && "clientId" in v) {
          onPick(v.clientId);
        }
      }}
      inputValue={clientId}
      onInputValueChange={(v) => onFreeText(v ?? "")}
      disabled={disabled}
    >
      <ComboboxInput
        placeholder={loading ? "Loading clients…" : "abc123…"}
        className="font-mono text-xs"
        disabled={disabled}
      />
      <ComboboxContent>
        <ComboboxEmpty>
          {clientId
            ? `Use "${clientId}" (paste client ID directly)`
            : "No app clients in this pool."}
        </ComboboxEmpty>
        <ComboboxList>
          {(item: CognitoUserPoolClient) => (
            <ComboboxItem key={item.clientId} value={item}>
              <div className="flex min-w-0 flex-1 items-center justify-between gap-2">
                <span className="truncate text-sm">{item.clientName || item.clientId}</span>
                <span className="text-muted-foreground text-2xs truncate font-mono">
                  {item.clientId}
                </span>
              </div>
            </ComboboxItem>
          )}
        </ComboboxList>
      </ComboboxContent>
    </Combobox>
  );
}
